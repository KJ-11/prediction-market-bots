# Bot entry point. Setup engines, risk, strategies. Main discovery loop.
# Discovers active 15-min crypto markets, delegates each round to round.py.

"""Kalshi 15-min crypto bot — multi-coin entry point.

Watches BTC, ETH, XRP simultaneously in a single process.
Uses SpotDistanceStrategy: trades when spot is >0.15% from strike in T+250-500.

Usage:
    python -m bots.kalshi_crypto.main --series all
    python -m bots.kalshi_crypto.main --series KXETH15M  # single coin
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from bots.kalshi_crypto.discovery import (
    SERIES_TO_COIN,
    discover_active_market,
    discover_all_markets,
)
from bots.kalshi_crypto.round import _sleep_until_midnight_cst, run_round
from bots.kalshi_crypto.sizing import PositionSizer, SizingMode
from bots.kalshi_crypto.strategies.cascade import CascadeStrategy
from bots.kalshi_crypto.strategy import BaseStrategy, RoundContext
from shared.alerts.manager import CST, AlertManager
from shared.clients.kalshi import KalshiClient
from shared.clients.polymarket import PolymarketClient
from shared.config import Settings
from shared.execution.kalshi import KalshiExecutionEngine
from shared.execution.paper import PaperExecutionEngine
from shared.risk import CircuitBreaker, KillSwitch, KillSwitchTriggered, RiskLimits
from shared.runner import BotRunner
from shared.summary import midnight_summary_loop
from shared.trade_log import TradeLog
from shared.types import PriceUpdate
from shared.utils.logging import setup_logging
from shared.ws.kalshi import KalshiWSManager
from shared.ws.spot import SpotPriceUpdate, SpotWSFeed

logger = logging.getLogger(__name__)

DISCOVERY_INTERVAL = 15.0
FLOOR_STRIKE_TIMEOUT = 300.0  # 5 min max wait for missing floor_strike


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kalshi 15-min crypto bot")
    parser.add_argument(
        "--series", default="all",
        help="Series ticker or 'all' for multi-coin (default: all)",
    )
    return parser.parse_args()


def _round_window(ctx: RoundContext) -> str:
    """Format round time window in CST, e.g. '13:00–13:15'."""
    open_cst = ctx.open_time.astimezone(CST)
    close_cst = ctx.close_time.astimezone(CST)
    return f"{open_cst.strftime('%H:%M')}\u2013{close_cst.strftime('%H:%M')}"


def _make_crypto_summary_fn(
    alerts: AlertManager,
    engine,
    shadow_engine,
    daily_stats: dict,
):
    """Build the per-midnight summary callback for the crypto bot."""
    async def summary_fn() -> None:
        date_str = datetime.now(CST).strftime("%b %-d")
        balance = await engine.get_balance()

        shadow_line = None
        if shadow_engine is not None:
            shadow_bal = await shadow_engine.get_balance()
            shadow_pnl = shadow_bal - Decimal("50")
            shadow_line = f"P&L: ${shadow_pnl:+.2f} | Balance: ${shadow_bal:.2f}"

        await alerts.daily_summary(
            date_str=date_str,
            trades=daily_stats["trades"],
            wins=daily_stats["wins"],
            pnl=daily_stats["pnl"],
            balance=balance,
            total_signals=daily_stats["signals"],
            shadow_line=shadow_line,
        )
        daily_stats["trades"] = 0
        daily_stats["wins"] = 0
        daily_stats["pnl"] = Decimal("0")
        daily_stats["signals"] = 0
    return summary_fn


async def run_bot(
    settings: Settings,
    series_arg: str,
    runner: BotRunner,
    alerts: AlertManager,
) -> None:
    """Main bot loop: discover -> subscribe -> run strategies -> repeat."""
    # Determine which series to watch
    if series_arg.lower() == "all":
        series_list = ["KXBTC15M", "KXETH15M", "KXXRP15M"]
    else:
        if series_arg not in SERIES_TO_COIN:
            logger.error("Unknown series: %s", series_arg)
            return
        series_list = [series_arg]

    coins = [SERIES_TO_COIN[s] for s in series_list]
    logger.info("Watching %d coins: %s", len(coins), ", ".join(coins))

    # Create client
    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_pem=settings.kalshi_private_key,
    )

    # Execution engine
    if settings.paper_trading:
        engine = PaperExecutionEngine(initial_balance=Decimal("50"))
        shadow_engine = None
        mode = "PAPER"
        logger.info("Mode: PAPER trading ($50 initial)")
    else:
        engine = KalshiExecutionEngine(
            client, price_cushion_cents=settings.price_cushion_cents,
        )
        shadow_engine = PaperExecutionEngine(
            initial_balance=Decimal("50"),
            balance_file=None,  # Shadow engine: no persistence, always fresh
        )
        mode = "LIVE"
        logger.info("Mode: LIVE trading (shadow paper engine active)")

    # Kill switch — wider threshold in paper mode to allow longer testing
    initial_balance = await engine.get_balance()
    loss_pct = 60.0 if settings.paper_trading else settings.max_capital_loss_pct
    kill_switch = KillSwitch(
        kill_file=settings.global_kill_file,
        max_loss_pct=loss_pct,
        max_consecutive_errors=settings.max_consecutive_errors,
        initial_balance=initial_balance,
    )

    # Risk limits — percentage-based so they scale with balance
    risk = RiskLimits(
        max_position_pct=settings.max_position_pct,
        max_exposure_pct=settings.max_exposure_pct,
        max_loss_per_trade_pct=settings.max_loss_per_trade_pct,
        max_orders_per_min=settings.max_orders_per_min,
    )

    # Circuit breaker — wider in paper mode for longer testing
    breaker = CircuitBreaker(
        max_consecutive_losses=3,
        daily_loss_limit_pct=50.0 if settings.paper_trading else 20.0,
        max_drawdown_pct=60.0 if settings.paper_trading else 40.0,
    )
    breaker.set_day_start_balance(initial_balance)

    # Position sizer — phased for $50 bankroll
    sizer = PositionSizer(
        mode=SizingMode.FRACTIONAL_KELLY,
        kelly_fraction=0.30,
    )

    # PM client for cascade strategy signals
    pm_client = PolymarketClient()
    pm_signals: dict[str, str | None] = {}  # shared dict: coin → "up"/"down"/None

    # Strategies — cascade (PM 5m → Kalshi YES) per coin
    # SpotDistanceStrategy disabled (negative EV, see strategy-evaluation.md)
    strategies: dict[str, BaseStrategy] = {
        series: CascadeStrategy(pm_signals) for series in series_list
    }

    # Spot WS feed — 1 connection for all coins
    spot_queue: asyncio.Queue[SpotPriceUpdate] = asyncio.Queue(maxsize=10000)
    spot_feed = SpotWSFeed(coins=coins, price_queue=spot_queue)
    await spot_feed.start()

    # Kalshi WS + price queue
    kalshi_queue: asyncio.Queue[PriceUpdate] = asyncio.Queue(maxsize=10000)
    kalshi_ws = KalshiWSManager(client, price_queue=kalshi_queue)

    # Trade log
    trade_log = TradeLog(bot_name="kalshi-crypto-multi")

    # Daily stats for midnight summary
    daily_stats = {
        "trades": 0,
        "wins": 0,
        "pnl": Decimal("0"),
        "signals": 0,
    }

    # Send bot started alert (now that we have mode/coins/balance)
    await alerts.bot_started(mode=mode, coins=coins, balance=initial_balance)

    # Start midnight summary task
    summary_task = asyncio.create_task(
        midnight_summary_loop(
            _make_crypto_summary_fn(alerts, engine, shadow_engine, daily_stats),
        ),
        name="midnight-summary",
    )

    # Track how long we've been waiting with no active markets (floor_strike timeout)
    no_market_since: float | None = None
    # Use locally-computed balance for risk checks to avoid Kalshi settlement lag.
    # engine.get_balance() can show a false drop when contracts are bought but
    # winnings haven't been credited yet.
    local_balance: Decimal | None = None

    try:
        while not runner.shutdown_requested:
            # Use local balance if available (accurate), fall back to API
            if local_balance is not None:
                balance = local_balance
            else:
                balance = await engine.get_balance()
            try:
                kill_switch.check(balance)
                breaker.check(balance)
            except KillSwitchTriggered as e:
                logger.warning("KILL SWITCH: %s", e)
                await alerts.kill_switch_triggered(str(e))
                # Sleep until midnight CST instead of exiting, so Docker
                # doesn't restart-loop us.
                await _sleep_until_midnight_cst(runner)
                # After midnight, reset ATH + breaker for fresh start
                balance = await engine.get_balance()
                breaker.reset_ath(balance)
                breaker.set_day_start_balance(balance)
                local_balance = None  # Force fresh balance from API
                continue

            if breaker.stopped_for_day:
                logger.warning("Circuit breaker: stopped for day")
                await alerts.circuit_breaker(
                    f"Daily loss limit hit — balance ${balance:.2f}, stopped for day"
                )
                # Sleep until midnight CST instead of exiting, so Docker
                # doesn't restart-loop us back into the same breaker state.
                await _sleep_until_midnight_cst(runner)
                # After midnight, reset breaker for new day
                breaker.set_day_start_balance(balance)
                continue

            if breaker.should_skip_round:
                logger.info("Circuit breaker: skipping round (consecutive losses)")
                await alerts.circuit_breaker(
                    "3 consecutive losses — skipping next round"
                )
                breaker.clear_skip()
                await asyncio.sleep(DISCOVERY_INTERVAL)
                continue

            # Discover all active markets
            try:
                if len(series_list) > 1:
                    market_map = await discover_all_markets(client, series_list)
                else:
                    ctx = await discover_active_market(client, series_list[0])
                    market_map = {series_list[0]: ctx}
            except Exception as e:
                logger.warning("Discovery failed: %s, retrying in %.0fs", e, DISCOVERY_INTERVAL)
                await asyncio.sleep(DISCOVERY_INTERVAL)
                continue

            active_contexts = {s: c for s, c in market_map.items() if c is not None}

            if not active_contexts:
                now = time.monotonic()
                if no_market_since is None:
                    no_market_since = now
                waited = now - no_market_since
                if waited >= FLOOR_STRIKE_TIMEOUT:
                    logger.warning(
                        "No active markets for %.0fs (floor_strike timeout), "
                        "skipping to next round",
                        waited,
                    )
                    no_market_since = None
                    # Sleep until next 15-min boundary + 30s buffer
                    utcnow = datetime.now(timezone.utc)
                    mins_past = utcnow.minute % 15
                    wait_secs = (15 - mins_past) * 60 - utcnow.second + 30
                    if wait_secs > 0:
                        logger.info("Sleeping %.0fs until next round", wait_secs)
                        await asyncio.sleep(wait_secs)
                    continue
                logger.debug(
                    "No active markets (%.0fs waited), waiting %.0fs...",
                    waited, DISCOVERY_INTERVAL,
                )
                try:
                    await asyncio.wait_for(
                        runner.shutdown_event.wait(), timeout=DISCOVERY_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    pass
                continue
            # Reset floor_strike timeout once we find active markets
            no_market_since = None

            # Retry discovery if we found some but not all markets
            # (markets open with slight delays between coins)
            if len(active_contexts) < len(series_list):
                for _ in range(3):
                    await asyncio.sleep(5)
                    market_map = await discover_all_markets(
                        client, series_list,
                    )
                    active_contexts = {
                        s: c for s, c in market_map.items()
                        if c is not None
                    }
                    if len(active_contexts) >= len(series_list):
                        break

            # Run round with all active markets
            any_ctx = next(iter(active_contexts.values()))
            window = _round_window(any_ctx)
            round_coins = [c.coin for c in active_contexts.values()]
            # Use local balance if available to avoid settlement lag
            if local_balance is not None:
                balance = local_balance
            else:
                balance = await engine.get_balance()

            logger.info(
                "=== ROUND START: %d markets [%s] ===",
                len(active_contexts), ", ".join(round_coins),
            )
            await alerts.round_start(
                window=window, coins=round_coins, balance=balance,
            )

            # Reset PM signals for new round
            pm_signals.clear()

            round_result = await run_round(
                active_contexts=active_contexts,
                strategies=strategies,
                engine=engine,
                shadow_engine=shadow_engine,
                client=client,
                kill_switch=kill_switch,
                risk=risk,
                sizer=sizer,
                kalshi_ws=kalshi_ws,
                kalshi_queue=kalshi_queue,
                spot_queue=spot_queue,
                runner=runner,
                trade_log=trade_log,
                alerts=alerts,
                window=window,
                round_start_balance=balance,
                pm_client=pm_client,
                pm_signals=pm_signals,
            )

            # Update circuit breaker and daily stats
            # Use locally-computed balance (Kalshi API has settlement lag)
            balance = round_result["balance_after"]
            local_balance = balance
            if round_result["trades"] > 0:
                breaker.record_round_result(
                    won=round_result["pnl"] > 0, current_balance=balance,
                )
            daily_stats["trades"] += round_result["trades"]
            daily_stats["wins"] += round_result["wins"]
            daily_stats["pnl"] += round_result["pnl"]
            daily_stats["signals"] += round_result["signals"]

            logger.info("=== ROUND END ===")

            # Brief pause between rounds
            if not runner.shutdown_requested:
                await asyncio.sleep(2.0)

    finally:
        summary_task.cancel()
        try:
            await summary_task
        except asyncio.CancelledError:
            pass

        trade_log.close()
        await spot_feed.stop()
        await kalshi_ws.stop()
        await client.close()
        await pm_client.close()


def main() -> None:
    args = parse_args()
    settings = Settings()

    setup_logging(
        level=settings.log_level, fmt=settings.log_format,
        bot_name="kalshi-crypto",
    )

    if args.series.lower() == "all":
        bot_name = "kalshi-crypto-multi"
    else:
        bot_name = f"kalshi-crypto-{args.series}"

    alerts = AlertManager(
        bot_name=bot_name,
        telegram_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
    )

    runner = BotRunner(bot_name=bot_name, alerts=alerts)
    exit_code = runner.run(run_bot, settings, args.series, runner, alerts)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
