"""Kalshi 15-min crypto bot — multi-coin entry point.

Watches BTC, ETH, SOL simultaneously in a single process.
Uses SpotDistanceStrategy: trades when spot is >0.2% from strike in T+600-800.

Usage:
    python -m bots.kalshi_crypto.main --series all
    python -m bots.kalshi_crypto.main --series KXETH15M  # single coin
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from bots.kalshi_crypto.discovery import (
    SERIES_TO_COIN,
    discover_active_market,
    discover_all_markets,
)
from bots.kalshi_crypto.sizing import PositionSizer, SizingMode
from bots.kalshi_crypto.strategies.spot_distance import SpotDistanceStrategy
from bots.kalshi_crypto.strategy import RoundContext, TradeSignal
from shared.alerts.manager import CST, AlertManager
from shared.clients.kalshi import KalshiClient
from shared.config import Settings
from shared.execution.kalshi import KalshiExecutionEngine
from shared.execution.paper import PaperExecutionEngine
from shared.risk import CircuitBreaker, KillSwitch, RiskLimits
from shared.runner import BotRunner
from shared.trade_log import TradeLog
from shared.types import OrderStatus, Outcome, PriceUpdate
from shared.utils.logging import setup_logging
from shared.ws.kalshi import KalshiWSManager
from shared.ws.spot import COIN_TO_COINBASE, SpotPriceUpdate, SpotWSFeed

logger = logging.getLogger(__name__)

DISCOVERY_INTERVAL = 15.0
QUEUE_DRAIN_TIMEOUT = 0.5


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


async def _midnight_summary_task(
    alerts: AlertManager,
    engine,
    shadow_engine,
    daily_stats: dict,
) -> None:
    """Fire daily summary at midnight CST, then reset stats."""
    while True:
        now = datetime.now(CST)
        # Next midnight CST
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        wait_seconds = (tomorrow - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # Send summary
        date_str = now.strftime("%b %-d")
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

        # Reset daily stats
        daily_stats["trades"] = 0
        daily_stats["wins"] = 0
        daily_stats["pnl"] = Decimal("0")
        daily_stats["signals"] = 0


async def run_bot(
    settings: Settings,
    series_arg: str,
    runner: BotRunner,
    alerts: AlertManager,
) -> None:
    """Main bot loop: discover -> subscribe -> run strategies -> repeat."""
    # Determine which series to watch
    if series_arg.lower() == "all":
        series_list = ["KXBTC15M", "KXETH15M", "KXSOL15M"]
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
        engine = KalshiExecutionEngine(client)
        shadow_engine = PaperExecutionEngine(initial_balance=Decimal("50"))
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
        kelly_fraction=0.25,
    )

    # Strategies — one SpotDistanceStrategy per coin
    strategies: dict[str, SpotDistanceStrategy] = {
        series: SpotDistanceStrategy() for series in series_list
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
        _midnight_summary_task(alerts, engine, shadow_engine, daily_stats),
        name="midnight-summary",
    )

    try:
        while not runner.shutdown_requested:
            # Positions are settled at round end, so balance is accurate
            balance = await engine.get_balance()
            kill_switch.check(balance)
            breaker.check(balance)

            if breaker.stopped_for_day:
                logger.warning("Circuit breaker: stopped for day")
                await alerts.circuit_breaker(
                    f"Daily loss limit hit — balance ${balance:.2f}, stopped for day"
                )
                break

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
                logger.debug("No active markets, waiting %.0fs...", DISCOVERY_INTERVAL)
                try:
                    await asyncio.wait_for(
                        runner.shutdown_event.wait(), timeout=DISCOVERY_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    pass
                continue

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
            balance = await engine.get_balance()

            logger.info(
                "=== ROUND START: %d markets [%s] ===",
                len(active_contexts), ", ".join(round_coins),
            )
            await alerts.round_start(
                window=window, coins=round_coins, balance=balance,
            )

            round_result = await _run_round(
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
            )

            # Update circuit breaker and daily stats
            balance = await engine.get_balance()
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


async def _run_round(
    active_contexts: dict[str, RoundContext],
    strategies: dict[str, SpotDistanceStrategy],
    engine,
    shadow_engine: PaperExecutionEngine | None,
    client: KalshiClient,
    kill_switch: KillSwitch,
    risk: RiskLimits,
    sizer: PositionSizer,
    kalshi_ws: KalshiWSManager,
    kalshi_queue: asyncio.Queue[PriceUpdate],
    spot_queue: asyncio.Queue[SpotPriceUpdate],
    runner: BotRunner,
    trade_log: TradeLog,
    alerts: AlertManager,
    window: str,
) -> dict:
    """Run all strategies for one 15-min round across all coins.

    Returns dict with round stats: trades, wins, pnl, signals.
    """
    # Subscribe to all active tickers
    tickers = [ctx.ticker for ctx in active_contexts.values()]
    kalshi_ws.set_tickers(tickers)
    kalshi_ws_task = asyncio.create_task(kalshi_ws.start(), name="kalshi-ws-round")

    # Build ticker -> series mapping for routing updates
    ticker_to_series: dict[str, str] = {
        ctx.ticker: series for series, ctx in active_contexts.items()
    }

    # Initialize strategies
    for series, strat in strategies.items():
        if series in active_contexts:
            strat.on_round_start(active_contexts[series])

    # Per-coin latest spot prices (keyed by Coinbase product_id -> series)
    coinbase_to_series: dict[str, str] = {}
    for series, ctx in active_contexts.items():
        coin = ctx.coin
        cb_id = COIN_TO_COINBASE.get(coin)
        if cb_id:
            coinbase_to_series[cb_id] = series

    latest_spots: dict[str, Decimal] = {}  # series -> price
    latest_kalshi: dict[str, PriceUpdate] = {}  # series -> update
    kalshi_update_counts: dict[str, int] = {}  # track updates per series

    round_signals = 0
    round_trades = 0
    round_wins = 0
    traded_coins: list[str] = []
    # Per-coin outcome lines for round summary
    coin_lines: list[str] = []
    # Track skip reasons per coin for summary
    skip_reasons: dict[str, str] = {}
    # Track live fills for accurate P&L (balance delta is unreliable for live)
    live_fills: list[dict] = []  # [{ticker, outcome, price, size, coin}, ...]
    market_results: dict[str, Outcome | None] = {}  # ticker -> winning outcome

    try:
        # Use any context to check timing (they all share the same schedule)
        any_ctx = next(iter(active_contexts.values()))

        while any_ctx.seconds_remaining() > 0 and not runner.shutdown_requested:
            # NOTE: Don't check kill switch mid-round — buying contracts
            # reduces balance but the value is in positions, not lost

            # Drain spot queue
            while True:
                try:
                    spot_update = spot_queue.get_nowait()
                    series = coinbase_to_series.get(spot_update.symbol)
                    if series:
                        latest_spots[series] = spot_update.price
                except asyncio.QueueEmpty:
                    break

            # Drain Kalshi queue
            while True:
                try:
                    kalshi_update = kalshi_queue.get_nowait()
                    series = ticker_to_series.get(kalshi_update.market_id)
                    if series:
                        latest_kalshi[series] = kalshi_update
                        kalshi_update_counts[series] = (
                            kalshi_update_counts.get(series, 0) + 1
                        )
                except asyncio.QueueEmpty:
                    break

            # Only run strategies once we have real Kalshi data
            # (need at least 3 updates per coin to avoid stale initial data)
            min_updates = 3
            coins_warmed = sum(
                1 for c in kalshi_update_counts.values() if c >= min_updates
            )
            if coins_warmed < len(active_contexts):
                try:
                    await asyncio.wait_for(
                        _wait_for_any(kalshi_queue, spot_queue),
                        timeout=QUEUE_DRAIN_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            # Run spot distance strategy per coin
            all_signals: list[TradeSignal] = []
            for series, strat in strategies.items():
                if series not in active_contexts:
                    continue
                ctx = active_contexts[series]
                spot = latest_spots.get(series)
                kalshi = latest_kalshi.get(series)
                signals = strat.on_update(ctx, kalshi, spot)
                all_signals.extend(signals)

            # Execute signals (cap at 3 per round — one per coin max)
            max_trades_per_round = 3
            for signal in all_signals:
                round_signals += 1
                sig_series = ticker_to_series.get(signal.order.market_id)
                coin = SERIES_TO_COIN.get(sig_series, "?") if sig_series else "?"

                # Log signal to file only (no Telegram spam)
                alerts.log_only(
                    "SIGNAL",
                    f"{coin}: {signal.reason} (confidence={signal.confidence:.3f})",
                )

                if round_trades >= max_trades_per_round:
                    logger.info(
                        "Skipping signal (round trade cap): %s",
                        signal.reason,
                    )
                    skip_reasons[coin] = "round trade cap"
                    break

                balance = await engine.get_balance()

                # Get available ask size for liquidity cap
                sig_kalshi = latest_kalshi.get(sig_series) if sig_series else None
                if sig_kalshi is not None:
                    if signal.order.outcome == Outcome.YES:
                        available_size = sig_kalshi.yes_ask_size
                    else:
                        # For NO outcome, bid side of YES is the ask for NO
                        available_size = sig_kalshi.yes_bid_size
                else:
                    available_size = None

                # Shadow paper execution (always fills instantly)
                if shadow_engine is not None:
                    shadow_bal = await shadow_engine.get_balance()
                    shadow_size = sizer.compute(
                        signal.order.price, signal.confidence, shadow_bal,
                    )
                    if shadow_size > 0:
                        shadow_order = copy.deepcopy(signal.order)
                        shadow_order.size = Decimal(str(shadow_size))
                        await shadow_engine.place_order(shadow_order)

                executed, skip_reason = await _execute_signal(
                    signal, engine, risk, sizer, balance,
                    kill_switch, trade_log, alerts,
                    available_size=available_size,
                    coin=coin,
                )
                if executed:
                    round_trades += 1
                    if coin not in traded_coins:
                        traded_coins.append(coin)
                    live_fills.append({
                        "ticker": signal.order.market_id,
                        "outcome": signal.order.outcome,
                        "price": signal.order.price,
                        "size": signal.order.size,
                        "coin": coin,
                    })
                elif skip_reason:
                    skip_reasons[coin] = skip_reason

            # Wait for next update
            try:
                await asyncio.wait_for(
                    _wait_for_any(kalshi_queue, spot_queue),
                    timeout=QUEUE_DRAIN_TIMEOUT,
                )
            except asyncio.TimeoutError:
                pass

    finally:
        # Stop Kalshi WS
        await kalshi_ws.stop()
        kalshi_ws_task.cancel()
        try:
            await kalshi_ws_task
        except asyncio.CancelledError:
            pass

        # Cancel open orders
        for ctx in active_contexts.values():
            cancelled = await engine.cancel_all(market_id=ctx.ticker)
            if cancelled:
                logger.info("Cancelled %d orders for %s", cancelled, ctx.ticker)

        # Notify strategies
        for series, strat in strategies.items():
            if series in active_contexts:
                strat.on_round_end()

        # Settle positions in paper mode using Kalshi API resolution
        engines_to_settle: list[tuple[str, PaperExecutionEngine]] = []
        if isinstance(engine, PaperExecutionEngine):
            engines_to_settle.append(("paper", engine))
        if shadow_engine is not None:
            engines_to_settle.append(("shadow", shadow_engine))

        if engines_to_settle:
            # Wait for settlement only if we didn't already wait for live fills
            if not live_fills:
                await asyncio.sleep(15)
            for series, ctx in active_contexts.items():
                # Reuse market results already fetched for live P&L
                winning = market_results.get(ctx.ticker)
                if winning is None:
                    try:
                        market = await client.fetch_market(ctx.ticker)
                        result_str = market.get("result") if market else None
                        if result_str not in ("yes", "no"):
                            await asyncio.sleep(30)
                            market = await client.fetch_market(ctx.ticker)
                            result_str = market.get("result") if market else None
                    except Exception as e:
                        logger.warning("Settlement fetch failed for %s: %s", ctx.ticker, e)
                        result_str = None
                    if result_str == "yes":
                        winning = Outcome.YES
                    elif result_str == "no":
                        winning = Outcome.NO
                    else:
                        spot = latest_spots.get(series)
                        if spot is None:
                            continue
                        winning = Outcome.YES if spot > ctx.floor_strike else Outcome.NO
                        logger.warning(
                            "Kalshi not settled for %s, using spot fallback",
                            ctx.ticker,
                        )
                for label, eng in engines_to_settle:
                    settle_pnl = await eng.settle_market(
                        ctx.ticker, winning,
                    )
                    if settle_pnl != 0:
                        logger.info(
                            "[%s] Settled %s: %s won, pnl=$%+.2f",
                            label, ctx.ticker, winning.value, settle_pnl,
                        )

        # Wait for Kalshi to settle live fills before checking balance
        if live_fills and not isinstance(engine, PaperExecutionEngine):
            await asyncio.sleep(15)

        # Compute P&L from actual fills + market resolution (not balance delta)
        round_pnl = Decimal("0")
        round_wins = 0
        market_results: dict[str, Outcome | None] = {}

        if live_fills:
            # Fetch market results for tickers we traded
            traded_tickers = {f["ticker"] for f in live_fills}
            for ticker in traded_tickers:
                try:
                    market = await client.fetch_market(ticker)
                    result_str = market.get("result") if market else None
                    if result_str == "yes":
                        market_results[ticker] = Outcome.YES
                    elif result_str == "no":
                        market_results[ticker] = Outcome.NO
                    else:
                        # Not settled yet, wait more
                        await asyncio.sleep(30)
                        market = await client.fetch_market(ticker)
                        result_str = market.get("result") if market else None
                        if result_str == "yes":
                            market_results[ticker] = Outcome.YES
                        elif result_str == "no":
                            market_results[ticker] = Outcome.NO
                        else:
                            market_results[ticker] = None
                except Exception as e:
                    logger.warning("Failed to fetch result for %s: %s", ticker, e)
                    market_results[ticker] = None

            # Calculate P&L per fill and build coin lines
            for fill in live_fills:
                winner = market_results.get(fill["ticker"])
                if winner is None:
                    logger.warning("No result for %s, can't compute P&L", fill["ticker"])
                    coin_lines.append(f"{fill['coin']}: result unknown")
                    continue
                price = fill["price"]
                size = fill["size"]
                # Fee: ceil(0.07 * contracts * P * (1-P))
                fee_raw = Decimal("0.07") * size * price * (1 - price)
                fee = (fee_raw * 100).to_integral_value() / 100  # ceil to cent
                if fill["outcome"] == winner:
                    # Won: revenue = $1 * size, cost = price * size
                    fill_pnl = (1 - price) * size - fee
                    round_wins += 1
                    coin_lines.append(
                        f"\u2705 {fill['coin']}: {fill['outcome'].value.upper()} "
                        f"@ ${price} x{int(size)} \u2192 WON +${fill_pnl:.2f}"
                    )
                else:
                    # Lost: lose entire cost
                    fill_pnl = -price * size - fee
                    coin_lines.append(
                        f"\u274c {fill['coin']}: {fill['outcome'].value.upper()} "
                        f"@ ${price} x{int(size)} \u2192 LOST ${fill_pnl:.2f}"
                    )
                round_pnl += fill_pnl
                logger.info(
                    "Fill P&L: %s %s @ $%.2f x%s → %s won → $%+.4f (fee $%.4f)",
                    fill["outcome"].value, fill["ticker"],
                    price, size, winner.value, fill_pnl, fee,
                )

        # Add skip reasons for coins that had signals but didn't trade
        for coin, reason in skip_reasons.items():
            if coin not in traded_coins:
                coin_lines.append(f"{coin}: skipped ({reason})")

        balance_after = await engine.get_balance()

        for ctx in active_contexts.values():
            trade_log.log_round_summary(
                ctx.ticker, round_signals, round_trades, balance_after,
            )

        # Shadow summary line
        shadow_summary = None
        if shadow_engine is not None:
            shadow_bal = await shadow_engine.get_balance()
            shadow_pnl = shadow_bal - Decimal("50")
            if round_trades > 0 or round_signals > 0:
                shadow_summary = (
                    f"P&L: ${shadow_pnl:+.2f} | Balance: ${shadow_bal:.2f}"
                )
            else:
                shadow_summary = f"no trades | ${shadow_bal:.2f}"

        # Send consolidated round summary
        await alerts.round_summary(
            window=window,
            coin_lines=coin_lines,
            total_signals=round_signals,
            total_trades=round_trades,
            pnl=round_pnl if round_trades > 0 else None,
            balance=balance_after,
            shadow_summary=shadow_summary,
        )

        logger.info(
            "Round summary: signals=%d trades=%d pnl=$%.2f balance=$%.2f",
            round_signals, round_trades, round_pnl, balance_after,
        )
        if shadow_engine is not None:
            shadow_bal = await shadow_engine.get_balance()
            logger.info(
                "Shadow paper: balance=$%.2f pnl=$%+.2f",
                shadow_bal, shadow_bal - Decimal("50"),
            )

    return {
        "trades": round_trades,
        "wins": round_wins,
        "pnl": round_pnl,
        "signals": round_signals,
    }


async def _execute_signal(
    signal: TradeSignal,
    engine,
    risk: RiskLimits,
    sizer: PositionSizer,
    balance: Decimal,
    kill_switch: KillSwitch,
    trade_log: TradeLog,
    alerts: AlertManager,
    available_size: Decimal | None = None,
    coin: str = "",
) -> tuple[bool, str | None]:
    """Apply sizing and risk checks, then execute a trade signal.

    Returns (executed, skip_reason) — skip_reason is set when not executed.
    """
    strategy_name = signal.reason.split(":")[0].strip()
    round_ticker = signal.order.market_id

    # Compute size at the ask price
    size = sizer.compute(signal.order.price, signal.confidence, balance)
    if size <= 0:
        logger.info(
            "Signal skipped (size=0 at price=%s, bal=$%.2f): %s",
            signal.order.price, balance, signal.reason,
        )
        alerts.log_only(
            "SKIP",
            f"{coin}: price ${signal.order.price} too high, no edge after fees",
        )
        return False, f"price ${signal.order.price}, no edge"

    # Liquidity cap: don't try to buy more than what's on the book
    if available_size is not None and available_size > 0:
        available_int = int(available_size)
        if size > available_int:
            logger.info(
                "Liquidity cap: %d -> %d contracts (ask_size=%s)",
                size, available_int, available_size,
            )
            size = available_int
        if size <= 0:
            logger.info("Signal skipped (no liquidity at ask): %s", signal.reason)
            alerts.log_only("SKIP", f"{coin}: no liquidity at ask")
            return False, "no liquidity"

    signal.order.size = Decimal(str(size))
    signal.order.market_order = True  # Market order for immediate fill

    # Risk check
    result = await risk.check(signal.order, engine)
    if not result.allowed:
        logger.warning("Signal blocked by risk: %s — %s", signal.reason, result.reason)
        alerts.log_only("SKIP", f"{coin}: blocked by risk — {result.reason}")
        trade_log.log_signal(
            round_ticker=round_ticker,
            strategy=strategy_name,
            side=signal.order.side.value,
            outcome=signal.order.outcome.value,
            price=signal.order.price,
            size=signal.order.size,
            confidence=signal.confidence,
            reason=signal.reason,
            status=f"blocked:{result.reason}",
        )
        return False, f"risk: {result.reason}"

    # Execute (market order — fills at best price, no resting)
    logger.info(
        "Executing: %s %s (market) x%d — %s",
        signal.order.side.value, signal.order.outcome.value,
        size, signal.reason,
    )
    try:
        response = await engine.place_order(signal.order)
        risk.record_order()
        kill_switch.clear_errors()

        new_balance = await engine.get_balance()

        trade_log.log_signal(
            round_ticker=round_ticker,
            strategy=strategy_name,
            side=signal.order.side.value,
            outcome=signal.order.outcome.value,
            price=signal.order.price,
            size=signal.order.size,
            confidence=signal.confidence,
            reason=signal.reason,
            order_id=response.order_id,
            status=response.status.value,
            fill_price=response.avg_fill_price,
            fill_size=response.filled_size,
            balance_after=new_balance,
        )

        if response.status == OrderStatus.FAILED:
            error_detail = response.raw.get("error", "unknown") if response.raw else "unknown"
            logger.warning("Order failed: %s", error_detail)
            alerts.log_only(
                "ORDER_FAILED",
                f"{coin}: {error_detail} (price=${signal.order.price}, size={size})",
            )
            kill_switch.record_error()
            return False, f"order failed: {error_detail}"

        # If order is resting (not immediately filled), wait briefly then cancel
        if response.status in (OrderStatus.OPEN, OrderStatus.PENDING):
            logger.info(
                "Order resting (not filled): %s — waiting 5s",
                response.order_id,
            )
            await asyncio.sleep(5)
            # Cancel the resting order
            cancelled = await engine.cancel_order(response.order_id)
            if cancelled:
                logger.info("Cancelled unfilled order %s", response.order_id)
            else:
                logger.info("Order %s may have filled during wait", response.order_id)
            alerts.log_only(
                "SKIP",
                f"{coin}: order rested at ${signal.order.price}, cancelled",
            )
            return False, "order not filled"

        # Alert: trade filled (immediate Telegram notification)
        cost = signal.order.price * signal.order.size
        await alerts.trade_filled(
            coin=coin,
            side=signal.order.side.value.upper(),
            outcome=signal.order.outcome.value.upper(),
            price=signal.order.price,
            size=size,
            cost=cost,
            balance=new_balance,
        )

        logger.info(
            "Order filled: %s status=%s fill=%s",
            response.order_id, response.status.value, response.filled_size,
        )
        return True, None

    except Exception as e:
        logger.error("Order execution error: %s", e)
        kill_switch.record_error()
        return False, f"error: {e}"


async def _wait_for_any(*queues: asyncio.Queue) -> None:
    """Wait until any of the queues has an item."""
    while True:
        for q in queues:
            if not q.empty():
                return
        await asyncio.sleep(0.05)


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
