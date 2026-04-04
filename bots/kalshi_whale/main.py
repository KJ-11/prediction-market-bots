# Bot entry point. Setup engines, risk, whale detection. Main loop.
# Discovers near-close markets, detects whale trades via WS, enters positions.

"""Kalshi whale-following bot — entry point.

Four concurrent loops:
1. Market discovery (REST, every ~60s) — builds watchlist
2. Whale detection (WS trade channel) — detects whale trades, emits signals
3. Signal consumer — scores signals, enters positions
4. Position monitor (WS ticker + REST poll) — stop loss + settlement detection

Usage:
    python -m bots.kalshi_whale.main
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from bots.kalshi_whale.discovery import Watchlist, discover_markets
from bots.kalshi_whale.monitor import PositionMonitor, TrackedPosition
from bots.kalshi_whale.signal import WhaleDetector
from bots.kalshi_whale.sizing import compute_size, kalshi_fee
from bots.kalshi_whale.strategy import WhaleConfig, WhaleSignal
from bots.kalshi_whale.tracking import WhaleTracker
from shared.alerts.manager import CST, AlertManager
from shared.clients.kalshi import KalshiClient
from shared.config import Settings
from shared.execution.kalshi import KalshiExecutionEngine
from shared.execution.paper import PaperExecutionEngine
from shared.risk import CircuitBreaker, KillSwitch, KillSwitchTriggered, RiskLimits
from shared.runner import BotRunner
from shared.types import OrderRequest, OrderStatus, Outcome, PriceUpdate, Side
from shared.utils.logging import setup_logging

logger = logging.getLogger(__name__)

DISCOVERY_INTERVAL = 60.0  # Poll for new markets every 60s
STATS_LOG_INTERVAL = 300.0  # Log stats every 5 min


async def _discovery_loop(
    client: KalshiClient,
    config: WhaleConfig,
    watchlist: Watchlist,
    tracker: WhaleTracker,
    runner: BotRunner,
) -> None:
    """Continuously discover markets and update watchlist."""
    while not runner.shutdown_requested:
        try:
            markets = await discover_markets(client, config)

            added = 0
            for mkt in markets:
                if watchlist.add(mkt):
                    added += 1

            if added > 0:
                logger.info(
                    "Watchlist: %d markets (%d new)",
                    len(watchlist.tickers), added,
                )
                tracker.log_watchlist(len(watchlist.tickers), added)

        except Exception as e:
            logger.error("Discovery error: %s", e)

        try:
            await asyncio.wait_for(
                runner.shutdown_event.wait(), timeout=DISCOVERY_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


async def _signal_consumer(
    signal_queue: asyncio.Queue[WhaleSignal],
    config: WhaleConfig,
    engine,
    kill_switch: KillSwitch,
    risk: RiskLimits,
    breaker: CircuitBreaker,
    monitor: PositionMonitor,
    tracker: WhaleTracker,
    alerts: AlertManager,
    watchlist: Watchlist,
    runner: BotRunner,
    daily_stats: dict,
    bal: dict,
) -> None:
    """Consume whale signals, apply sizing + risk checks, enter positions."""
    # Track entered event_tickers to avoid betting both sides of the same game
    entered_events: set[str] = set()

    while not runner.shutdown_requested:
        try:
            signal = await asyncio.wait_for(signal_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            continue

        daily_stats["signals"] += 1

        logger.info(
            "Processing signal: %s %s — %d whales, %.0f%% consensus",
            signal.market_ticker, signal.side, signal.whale_count,
            signal.consensus_pct * 100,
        )

        # Dedup: don't bet on two markets in the same event (e.g. both teams)
        wl_market = watchlist.get(signal.market_ticker)
        event_ticker = wl_market.event_ticker if wl_market else ""
        if event_ticker and event_ticker in entered_events:
            logger.info(
                "Signal skipped: already entered event %s",
                event_ticker,
            )
            continue

        # Check concurrent position limit
        if monitor.open_count >= config.max_concurrent:
            logger.info(
                "Signal skipped: max concurrent positions (%d/%d)",
                monitor.open_count, config.max_concurrent,
            )
            continue

        # Use local balance (tracks entry costs / settlement payouts)
        # to avoid Kalshi API settlement lag.
        # Equity = cash + open position costs (money locked in positions).
        # Use equity for risk checks so 2 full slots don't look like a drawdown.
        balance: Decimal = bal["balance"]
        equity: Decimal = balance + monitor.open_cost

        # Kill switch — file-based + error-based only (no permanent capital kill).
        # The 93% WR is self-correcting; permanent death from early variance
        # is worse than any drawdown.
        try:
            kill_switch.check()  # No balance arg → skip capital loss check
        except KillSwitchTriggered as e:
            logger.warning("KILL SWITCH: %s", e)
            await alerts.kill_switch_triggered(str(e))
            continue

        # Circuit breaker — checks equity for daily loss + drawdown.
        # 40% drawdown → pause (not permanent kill). Resets after 24h.
        try:
            breaker.check(equity)
        except KillSwitchTriggered as e:
            logger.warning("DRAWDOWN PAUSE: %s — pausing 24h", e)
            await alerts.kill_switch_triggered(f"24h pause: {e}")
            # Sleep 24h instead of exiting, then reset and continue
            await asyncio.sleep(86400)
            api_bal = await engine.get_balance()
            bal["balance"] = api_bal
            breaker.reset_ath(api_bal + monitor.open_cost)
            breaker.set_day_start_balance(api_bal + monitor.open_cost)
            continue

        if breaker.stopped_for_day:
            logger.info("Signal skipped: circuit breaker stopped for day")
            continue

        if breaker.should_skip_round:
            logger.info("Signal skipped: circuit breaker skip")
            breaker.clear_skip()
            continue

        # Compute position size — divide balance by available slots so we
        # always reserve capacity for concurrent positions.
        open_slots = config.max_concurrent - monitor.open_count
        sizing_balance = balance / Decimal(str(open_slots)) if open_slots > 0 else balance
        size = compute_size(signal.best_ask, sizing_balance)
        if size <= 0:
            logger.info("Signal skipped: computed size = 0 (bal=$%.2f, slot_bal=$%.2f)", balance, sizing_balance)
            continue

        # Risk check
        outcome = Outcome.YES if signal.side == "yes" else Outcome.NO
        order = OrderRequest(
            market_id=signal.market_ticker,
            side=Side.BUY,
            outcome=outcome,
            price=signal.best_ask,
            size=Decimal(str(size)),
        )

        risk_result = await risk.check(order, engine)
        if not risk_result.allowed:
            logger.info("Signal rejected by risk: %s", risk_result.reason)
            continue

        # Execute entry
        logger.info(
            "ENTERING: %s %s @ %.2f x%d (bal=$%.2f)",
            signal.market_ticker, signal.side, signal.best_ask, size, balance,
        )

        await alerts._send(
            "WHALE SIGNAL",
            "\U0001f433",
            (
                f"<b>{signal.market_ticker}</b>\n"
                f"Side: {signal.side.upper()} | "
                f"Whales: {signal.whale_count} | "
                f"Consensus: {signal.consensus_pct:.0%}\n"
                f"Entry: <b>${signal.best_ask}</b> x{size} | "
                f"Volume: ${signal.total_volume:.0f}"
            ),
        )

        try:
            resp = await engine.place_order(order)
        except Exception as e:
            logger.error("Entry order failed: %s: %s", signal.market_ticker, e)
            kill_switch.record_error()
            continue

        kill_switch.clear_errors()
        risk.record_order()

        if resp.status != OrderStatus.FILLED:
            logger.warning(
                "Entry not filled: %s status=%s",
                signal.market_ticker, resp.status.value,
            )
            tracker.log_entry(
                market_ticker=signal.market_ticker,
                side="buy",
                outcome=signal.side,
                ideal_price=signal.best_ask,
                actual_price=None,
                contracts=size,
                order_id=resp.order_id,
                order_status=resp.status.value,
            )
            continue

        # Entry filled — start monitoring
        fill_price = resp.avg_fill_price or signal.best_ask
        stop_price = fill_price * (1 - Decimal(str(config.stop_loss_pct)))
        filled_size = int(resp.filled_size)

        entry_fee = kalshi_fee(fill_price, filled_size)
        cost = fill_price * filled_size + entry_fee

        # Deduct cost from local balance immediately (don't wait for API)
        bal["balance"] -= cost
        balance = bal["balance"]

        tracker.log_entry(
            market_ticker=signal.market_ticker,
            side="buy",
            outcome=signal.side,
            ideal_price=signal.best_ask,
            actual_price=fill_price,
            contracts=filled_size,
            order_id=resp.order_id,
            order_status=resp.status.value,
            balance_after=balance,
        )

        await alerts.trade_filled(
            coin=signal.market_ticker,
            side="BUY",
            outcome=signal.side.upper(),
            price=fill_price,
            size=filled_size,
            cost=cost,
            balance=balance,
        )

        monitor.add_position(TrackedPosition(
            market_ticker=signal.market_ticker,
            side=signal.side,
            entry_price=fill_price,
            size=filled_size,
            stop_price=stop_price,
            order_id=resp.order_id,
        ))

        daily_stats["trades"] += 1
        if event_ticker:
            entered_events.add(event_ticker)

        logger.info(
            "ENTERED: %s %s @ %.2f x%d stop=%.2f bal=$%.2f",
            signal.market_ticker, signal.side, fill_price, filled_size,
            stop_price, balance,
        )


async def _results_consumer(
    monitor: PositionMonitor,
    breaker: CircuitBreaker,
    engine,
    daily_stats: dict,
    bal: dict,
    runner: BotRunner,
) -> None:
    """Consume position results (settlements/stop losses) and update breaker."""
    while not runner.shutdown_requested:
        try:
            ticker, pnl = await asyncio.wait_for(
                monitor.results.get(), timeout=5.0,
            )
        except asyncio.TimeoutError:
            continue

        # Credit pnl to local balance (settlement payout or stop loss proceeds).
        # For wins: pnl = payout - cost, but cost was already deducted on entry.
        # The actual credit is cost + pnl = payout.
        # For stop losses: pnl is negative, but we get sell proceeds back.
        # The monitor already computed pnl = (fill_price - entry_price) * size - exit_fee.
        # We need to credit back: entry_cost + pnl (which = sell_proceeds - exit_fee).
        # Simpler: just sync from the engine since settlement is done by now.
        api_balance = await engine.get_balance()
        bal["balance"] = api_balance
        balance = api_balance

        won = pnl > 0
        breaker.record_round_result(won=won, current_balance=balance)

        if won:
            daily_stats["wins"] += 1
        daily_stats["pnl"] += pnl

        logger.info(
            "Result: %s pnl=$%+.2f (%s) bal=$%.2f",
            ticker, pnl, "WIN" if won else "LOSS", balance,
        )


async def _midnight_summary_task(
    alerts: AlertManager,
    engine,
    daily_stats: dict,
) -> None:
    """Fire daily summary at midnight CST, then reset stats."""
    while True:
        now = datetime.now(CST)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        wait_seconds = (tomorrow - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        balance = await engine.get_balance()
        date_str = now.strftime("%b %-d")

        await alerts.daily_summary(
            date_str=date_str,
            trades=daily_stats["trades"],
            wins=daily_stats["wins"],
            pnl=daily_stats["pnl"],
            balance=balance,
            total_signals=daily_stats["signals"],
        )

        daily_stats["trades"] = 0
        daily_stats["wins"] = 0
        daily_stats["pnl"] = Decimal("0")
        daily_stats["signals"] = 0


async def run_bot(
    settings: Settings,
    runner: BotRunner,
    alerts: AlertManager,
) -> None:
    """Main bot entrypoint — runs all 4 concurrent loops."""
    config = WhaleConfig()

    # Create client
    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_pem=settings.kalshi_private_key,
    )

    # Execution engine
    if settings.paper_trading:
        engine = PaperExecutionEngine(initial_balance=Decimal("100"))
        mode = "PAPER"
        logger.info("Mode: PAPER trading ($100 initial)")
    else:
        engine = KalshiExecutionEngine(
            client, price_cushion_cents=settings.price_cushion_cents,
        )
        mode = "LIVE"
        logger.info("Mode: LIVE trading")

    initial_balance = await engine.get_balance()

    # Kill switch — file-based + error-based only.
    # No capital loss kill; drawdown handled by circuit breaker with 24h pause.
    kill_switch = KillSwitch(
        kill_file=settings.global_kill_file,
        max_loss_pct=999.0,  # Effectively disabled
        max_consecutive_errors=settings.max_consecutive_errors,
        initial_balance=initial_balance,
    )

    # Risk limits — whale bot uses aggressive sizing at low balances
    # (100% of slot balance). Limits must accommodate this.
    risk = RiskLimits(
        max_position_pct=100.0,       # Sizing already handles allocation
        max_exposure_pct=100.0,       # Both slots can be full simultaneously
        max_loss_per_trade_pct=100.0, # Sizing already handles allocation
        max_orders_per_min=settings.max_orders_per_min,
    )

    # Circuit breaker
    breaker = CircuitBreaker(
        max_consecutive_losses=3,
        daily_loss_limit_pct=50.0 if settings.paper_trading else 20.0,
        max_drawdown_pct=60.0 if settings.paper_trading else 40.0,
    )
    breaker.set_day_start_balance(initial_balance)

    # Event tracker (CSV + structured logging)
    tracker = WhaleTracker()

    # Shared state
    watchlist = Watchlist()
    signal_queue: asyncio.Queue[WhaleSignal] = asyncio.Queue()
    price_queue: asyncio.Queue[PriceUpdate] = asyncio.Queue(maxsize=50000)

    daily_stats = {
        "trades": 0,
        "wins": 0,
        "pnl": Decimal("0"),
        "signals": 0,
    }

    # Local balance tracking — avoids Kalshi API settlement lag.
    # Deducted on entry, synced from API on settlement/stop loss.
    bal = {"balance": initial_balance}

    # Components
    detector = WhaleDetector(
        client=client,
        config=config,
        watchlist=watchlist,
        signal_queue=signal_queue,
        price_queue=price_queue,
        tracker=tracker,
        alerts=alerts,
    )

    monitor = PositionMonitor(
        config=config,
        engine=engine,
        client=client,
        alerts=alerts,
        tracker=tracker,
        price_queue=price_queue,
    )

    # Send startup alert
    await alerts.bot_started(
        mode=mode,
        coins=list(config.categories),
        balance=initial_balance,
    )

    # Launch all concurrent tasks
    tasks = [
        asyncio.create_task(
            _discovery_loop(client, config, watchlist, tracker, runner),
            name="discovery",
        ),
        asyncio.create_task(
            detector.run(),
            name="whale-ws",
        ),
        asyncio.create_task(
            _signal_consumer(
                signal_queue, config, engine, kill_switch, risk, breaker,
                monitor, tracker, alerts, watchlist, runner, daily_stats, bal,
            ),
            name="signal-consumer",
        ),
        asyncio.create_task(
            monitor.run_price_monitor(),
            name="price-monitor",
        ),
        asyncio.create_task(
            monitor.run_settlement_poller(),
            name="settlement-poller",
        ),
        asyncio.create_task(
            _results_consumer(monitor, breaker, engine, daily_stats, bal, runner),
            name="results-consumer",
        ),
        asyncio.create_task(
            _midnight_summary_task(alerts, engine, daily_stats),
            name="midnight-summary",
        ),
    ]

    try:
        # Wait for shutdown signal or any task to crash
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_EXCEPTION,
        )

        # If a task crashed, log it and cancel the rest
        for task in done:
            if task.exception():
                logger.error(
                    "Task %s crashed: %s",
                    task.get_name(), task.exception(),
                )

    finally:
        # Cancel all tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Cleanup
        await detector.stop()
        tracker.close()
        await client.close()


def main() -> None:
    settings = Settings()

    setup_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        bot_name="kalshi-whale",
    )

    alerts = AlertManager(
        bot_name="kalshi-whale",
        telegram_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
    )

    runner = BotRunner(bot_name="kalshi-whale", alerts=alerts)
    exit_code = runner.run(run_bot, settings, runner, alerts)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
