# Round execution. Subscribe to markets, run strategies, execute signals, settle.
# Each 15-min round: subscribe to Kalshi WS, drain spot+book updates,
# evaluate strategies, execute fills, wait for settlement, compute P&L.

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from bots.kalshi_crypto.discovery import SERIES_TO_COIN
from bots.kalshi_crypto.sizing import PositionSizer
from bots.kalshi_crypto.strategies.spot_distance import SpotDistanceStrategy
from bots.kalshi_crypto.strategy import RoundContext, TradeSignal
from shared.alerts.manager import CST, AlertManager
from shared.clients.kalshi import KalshiClient
from shared.execution.paper import PaperExecutionEngine
from shared.risk import KillSwitch, RiskLimits
from shared.runner import BotRunner
from shared.trade_log import TradeLog
from shared.types import OrderStatus, Outcome, PriceUpdate
from shared.ws.kalshi import KalshiWSManager
from shared.ws.spot import COIN_TO_COINBASE, SpotPriceUpdate

logger = logging.getLogger(__name__)

QUEUE_DRAIN_TIMEOUT = 0.5


async def _sleep_until_midnight_cst(runner: BotRunner) -> None:
    """Sleep until midnight CST, or until shutdown is requested."""
    now = datetime.now(CST)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    sleep_secs = (tomorrow - now).total_seconds()
    logger.info("Sleeping %.0f seconds until midnight CST", sleep_secs)
    try:
        await asyncio.wait_for(
            runner.shutdown_event.wait(), timeout=sleep_secs,
        )
    except asyncio.TimeoutError:
        pass


async def _fetch_market_result(
    client: KalshiClient,
    ticker: str,
) -> Outcome | None:
    """Fetch market result from Kalshi API, retrying once after 30s if not settled."""
    try:
        market = await client.fetch_market(ticker)
        result_str = market.get("result") if market else None
        if result_str not in ("yes", "no"):
            await asyncio.sleep(30)
            market = await client.fetch_market(ticker)
            result_str = market.get("result") if market else None
    except Exception as e:
        logger.warning("Failed to fetch result for %s: %s", ticker, e)
        return None
    if result_str == "yes":
        return Outcome.YES
    elif result_str == "no":
        return Outcome.NO
    return None


def _compute_fill_pnl(
    fill: dict,
    winner: Outcome,
) -> tuple[Decimal, str]:
    """Compute P&L and format line for a single fill.

    Returns (pnl, formatted_line).
    """
    price = fill["price"]
    size = fill["size"]
    coin = fill["coin"]
    outcome_str = fill["outcome"].value.upper()
    # Fee: ceil(0.07 * contracts * P * (1-P))
    fee_raw = Decimal("0.07") * size * price * (1 - price)
    fee = (fee_raw * 100).to_integral_value() / 100  # ceil to cent
    if fill["outcome"] == winner:
        pnl = (1 - price) * size - fee
        line = (
            f"\u2705 {coin}: {outcome_str} "
            f"@ ${price} x{int(size)} \u2192 WON +${pnl:.2f}"
        )
    else:
        pnl = -price * size - fee
        line = (
            f"\u274c {coin}: {outcome_str} "
            f"@ ${price} x{int(size)} \u2192 LOST ${pnl:.2f}"
        )
    return pnl, line


async def run_round(
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
    round_start_balance: Decimal = Decimal("0"),
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
    shadow_fills: list[dict] = []  # same shape, for shadow engine
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
                        shadow_fills.append({
                            "ticker": signal.order.market_id,
                            "outcome": signal.order.outcome,
                            "price": signal.order.price,
                            "size": shadow_order.size,
                            "coin": coin,
                        })

                # Grab volume from latest ticker update for logging
                sig_volume = sig_kalshi.volume if sig_kalshi is not None else None

                executed, skip_reason = await _execute_signal(
                    signal, engine, risk, sizer, balance,
                    kill_switch, trade_log, alerts,
                    available_size=available_size,
                    market_volume=sig_volume,
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
                    winning = await _fetch_market_result(client, ctx.ticker)
                if winning is None:
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

        # Wait for Kalshi to publish market results before fetching them
        if live_fills and not isinstance(engine, PaperExecutionEngine):
            await asyncio.sleep(15)

        # Compute P&L from actual fills + market resolution (not balance delta)
        round_pnl = Decimal("0")
        round_wins = 0

        if live_fills:
            # Fetch market results for tickers we traded
            traded_tickers = {f["ticker"] for f in live_fills}
            for ticker in traded_tickers:
                if ticker not in market_results:
                    market_results[ticker] = await _fetch_market_result(
                        client, ticker,
                    )

            # Calculate P&L per fill and build coin lines
            for fill in live_fills:
                winner = market_results.get(fill["ticker"])
                if winner is None:
                    logger.warning("No result for %s, can't compute P&L", fill["ticker"])
                    coin_lines.append(f"{fill['coin']}: result unknown")
                    continue
                fill_pnl, line = _compute_fill_pnl(fill, winner)
                if fill["outcome"] == winner:
                    round_wins += 1
                coin_lines.append(line)
                round_pnl += fill_pnl
                logger.info(
                    "Fill P&L: %s %s @ $%.2f x%s → %s won → $%+.4f",
                    fill["outcome"].value, fill["ticker"],
                    fill["price"], fill["size"], winner.value, fill_pnl,
                )

        # Add skip reasons for coins that had signals but didn't trade
        for coin, reason in skip_reasons.items():
            if coin not in traded_coins:
                coin_lines.append(f"{coin}: skipped ({reason})")

        # Compute balance locally — Kalshi API has settlement lag and
        # may not reflect won positions yet
        balance_after = round_start_balance + round_pnl

        for ctx in active_contexts.values():
            trade_log.log_round_summary(
                ctx.ticker, round_signals, round_trades, balance_after,
            )

        # Shadow summary line — show per-trade details
        shadow_summary = None
        if shadow_engine is not None:
            shadow_bal = await shadow_engine.get_balance()
            if shadow_fills:
                shadow_lines = []
                shadow_round_pnl = Decimal("0")
                for sf in shadow_fills:
                    winner = market_results.get(sf["ticker"])
                    if winner is None:
                        shadow_lines.append(f"{sf['coin']}: result unknown")
                        continue
                    sf_pnl, sf_line = _compute_fill_pnl(sf, winner)
                    shadow_lines.append(sf_line)
                    shadow_round_pnl += sf_pnl
                detail = "\n".join(shadow_lines)
                shadow_summary = (
                    f"{detail}\n"
                    f"P&L: ${shadow_round_pnl:+.2f} | Balance: ${shadow_bal:.2f}"
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
        "balance_after": balance_after,
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
    market_volume: Decimal | None = None,
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
            ask_size=available_size,
            market_volume=market_volume,
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

        # Compute cost locally — Kalshi balance API has settlement lag
        fill_count = int(response.filled_size) if response.filled_size else 0
        if fill_count > 0:
            fill_cost = signal.order.price * fill_count
            fee_raw = Decimal("0.07") * fill_count * signal.order.price * (1 - signal.order.price)
            fill_fee = (fee_raw * 100).to_integral_value() / 100
            trade_cost = fill_cost + fill_fee
            new_balance = balance - trade_cost
        else:
            new_balance = balance

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
            ask_size=available_size,
            market_volume=market_volume,
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

        # IOC: order cancelled with no fills means price moved away
        if response.status == OrderStatus.CANCELLED and (
            not response.filled_size or response.filled_size == 0
        ):
            logger.info(
                "IOC order cancelled (no fill) — price moved from %s: %s",
                signal.order.price, response.order_id,
            )
            alerts.log_only(
                "SKIP",
                f"{coin}: IOC no fill at ${signal.order.price} (price moved)",
            )
            return False, "IOC no fill"

        # IOC orders should never rest — but handle gracefully if they do
        if response.status in (OrderStatus.OPEN, OrderStatus.PENDING):
            logger.warning(
                "Unexpected resting order %s (IOC should not rest) — cancelling",
                response.order_id,
            )
            await engine.cancel_order(response.order_id)
            alerts.log_only(
                "SKIP",
                f"{coin}: order unexpectedly rested at ${signal.order.price}, cancelled",
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
