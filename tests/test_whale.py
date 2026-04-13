"""Tests for whale-following bot — core logic, sizing, signal scoring."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from bots.kalshi_whale.discovery import Watchlist, WatchlistMarket, parse_ticker_date
from bots.kalshi_whale.monitor import PositionMonitor, TrackedPosition
from bots.kalshi_whale.signal import WhaleDetector
from bots.kalshi_whale.sizing import compute_size, kalshi_fee
from bots.kalshi_whale.strategy import (
    MarketWhaleState,
    WhaleConfig,
    WhaleSignal,
    WhaleTrade,
)
from bots.kalshi_whale.tracking import WhaleTracker
from shared.execution.kalshi import _compute_fill_price
from shared.execution.paper import PaperExecutionEngine
from shared.types import OrderRequest, Outcome, PriceUpdate, Side

# ── Helpers ──────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _trade(
    side: str = "yes",
    price: str = "0.90",
    size: str = "1200",
    minutes_ago: float = 0,
    trade_id: str = "",
    ticker: str = "KXMLBGAME-26APR03-NYY",
) -> WhaleTrade:
    p = Decimal(price)
    s = Decimal(size)
    return WhaleTrade(
        trade_id=trade_id or f"t-{side}-{price}-{minutes_ago}",
        market_ticker=ticker,
        price=p,
        size=s,
        notional=p * s,
        taker_side=side,
        timestamp=_now() - timedelta(minutes=minutes_ago),
    )


# ── parse_ticker_date ────────────────────────────────────────────────

class TestParseTickerDate:
    def test_standard_sports(self):
        assert parse_ticker_date("KXMLBGAME-26APR03-NYY") == date(2026, 4, 3)

    def test_match_format(self):
        assert parse_ticker_date("KXATPMATCH-26APR04TIRBUR-TIR") == date(2026, 4, 4)

    def test_economics(self):
        assert parse_ticker_date("KXWTI-26MAR31-T116.99") == date(2026, 3, 31)

    def test_ipl(self):
        assert parse_ticker_date("KXIPLGAME-26APR04MIDC") == date(2026, 4, 4)

    def test_nba_1h(self):
        assert parse_ticker_date("KXNBA1HTOTAL-26MAR31PORLAC-107") == date(2026, 3, 31)

    def test_no_date_long_duration(self):
        assert parse_ticker_date("KXCANADACUP-30") is None

    def test_no_date_trillionaire(self):
        assert parse_ticker_date("KXTRILLIONAIRE-30-EM") is None

    def test_no_date_season_wins(self):
        assert parse_ticker_date("KXMLBWINS-SOMETHING") is None

    def test_case_insensitive(self):
        assert parse_ticker_date("KXMLBGAME-26apr03-NYY") == date(2026, 4, 3)

    def test_december(self):
        assert parse_ticker_date("KXINXU-26DEC25H1000") == date(2026, 12, 25)

    def test_january(self):
        assert parse_ticker_date("KXTEST-27JAN01SOMETHING") == date(2027, 1, 1)

    def test_invalid_day(self):
        # Feb 30 doesn't exist
        assert parse_ticker_date("KXTEST-26FEB30") is None


# ── Fees ─────────────────────────────────────────────────────────────

class TestFees:
    def test_fee_at_50c(self):
        # 0.07 * 1 * 0.50 * 0.50 = 0.0175 → ceil = 0.02
        assert kalshi_fee(Decimal("0.50"), 1) == Decimal("0.02")

    def test_fee_at_90c(self):
        # 0.07 * 1 * 0.90 * 0.10 = 0.0063 → ceil = 0.01
        assert kalshi_fee(Decimal("0.90"), 1) == Decimal("0.01")

    def test_fee_at_95c(self):
        # 0.07 * 1 * 0.95 * 0.05 = 0.003325 → ceil = 0.01
        assert kalshi_fee(Decimal("0.95"), 1) == Decimal("0.01")

    def test_fee_multi_contract(self):
        # 0.07 * 10 * 0.90 * 0.10 = 0.063 → ceil = 0.07
        assert kalshi_fee(Decimal("0.90"), 10) == Decimal("0.07")

    def test_fee_large_order(self):
        # 0.07 * 100 * 0.90 * 0.10 = 0.63 → exact, no rounding needed
        assert kalshi_fee(Decimal("0.90"), 100) == Decimal("0.63")

    def test_fee_symmetric(self):
        # Fee at 0.10 should equal fee at 0.90 (P * (1-P) is symmetric)
        assert kalshi_fee(Decimal("0.10"), 1) == kalshi_fee(Decimal("0.90"), 1)


# ── Sizing ───────────────────────────────────────────────────────────

class TestSizing:
    def test_phase_100_pct(self):
        # $100 balance → 100% allocation
        size = compute_size(Decimal("0.90"), Decimal("100"))
        cost = Decimal("0.90") * size + kalshi_fee(Decimal("0.90"), size)
        assert size > 0
        assert cost <= Decimal("100")

    def test_phase_50_pct(self):
        # $500 balance → 50% allocation
        size = compute_size(Decimal("0.90"), Decimal("500"))
        cost = Decimal("0.90") * size + kalshi_fee(Decimal("0.90"), size)
        assert cost <= Decimal("250")  # 50% of 500

    def test_phase_30_pct(self):
        # $1000 balance → 30% allocation
        size = compute_size(Decimal("0.90"), Decimal("1000"))
        cost = Decimal("0.90") * size + kalshi_fee(Decimal("0.90"), size)
        assert cost <= Decimal("300")  # 30% of 1000

    def test_phase_20_pct(self):
        # $5000 balance → 20% allocation
        size = compute_size(Decimal("0.90"), Decimal("5000"))
        cost = Decimal("0.90") * size + kalshi_fee(Decimal("0.90"), size)
        assert cost <= Decimal("1000")  # 20% of 5000

    def test_phase_10_pct(self):
        # $50000 balance → 10% allocation
        size = compute_size(Decimal("0.90"), Decimal("50000"))
        cost = Decimal("0.90") * size + kalshi_fee(Decimal("0.90"), size)
        assert cost <= Decimal("5000")  # 10% of 50000

    def test_zero_balance(self):
        assert compute_size(Decimal("0.90"), Decimal("0")) == 0

    def test_negative_balance(self):
        assert compute_size(Decimal("0.90"), Decimal("-10")) == 0

    def test_price_at_boundary(self):
        assert compute_size(Decimal("0"), Decimal("100")) == 0
        assert compute_size(Decimal("1"), Decimal("100")) == 0

    def test_never_exceeds_balance(self):
        """Total cost (price * contracts + fees) must never exceed balance."""
        for bal in [50, 100, 499, 500, 999, 1000, 4999, 5000, 49999, 50000]:
            for price_str in ["0.85", "0.90", "0.95"]:
                price = Decimal(price_str)
                balance = Decimal(str(bal))
                size = compute_size(price, balance)
                if size > 0:
                    total = price * size + kalshi_fee(price, size)
                    assert total <= balance, (
                        f"bal={bal} price={price} size={size} cost={total}"
                    )


# ── MarketWhaleState ─────────────────────────────────────────────────

class TestMarketWhaleState:
    def test_empty_state(self):
        s = MarketWhaleState(market_ticker="TEST")
        assert s.whale_count == 0
        assert s.consensus_side is None
        assert s.consensus_pct == 0.0
        assert s.total_volume == Decimal("0")

    def test_single_trade(self):
        s = MarketWhaleState(market_ticker="TEST")
        s.add_trade(_trade(side="yes", price="0.90", size="1200"))
        assert s.whale_count == 1
        assert s.consensus_side == "yes"
        assert s.consensus_pct == 1.0
        assert s.yes_volume == Decimal("1080")  # 0.90 * 1200

    def test_consensus_yes(self):
        s = MarketWhaleState(market_ticker="TEST")
        s.add_trade(_trade(side="yes", price="0.90", size="2000"))  # $1800
        s.add_trade(_trade(side="yes", price="0.92", size="1500"))  # $1380
        s.add_trade(_trade(side="no", price="0.10", size="1000"))   # $100
        assert s.consensus_side == "yes"
        assert s.consensus_pct > 0.9

    def test_consensus_no(self):
        s = MarketWhaleState(market_ticker="TEST")
        s.add_trade(_trade(side="no", price="0.90", size="5000"))
        s.add_trade(_trade(side="yes", price="0.90", size="100"))
        assert s.consensus_side == "no"

    def test_recent_trades_window(self):
        s = MarketWhaleState(market_ticker="TEST")
        # Old trade (40 min ago, outside 30-min window)
        s.add_trade(_trade(side="yes", minutes_ago=40, trade_id="old"))
        # Recent trades (within 30-min window)
        s.add_trade(_trade(side="yes", minutes_ago=10, trade_id="new1"))
        s.add_trade(_trade(side="yes", minutes_ago=5, trade_id="new2"))

        assert s.whale_count == 3  # All trades counted
        recent = s.recent_trades(30)
        assert len(recent) == 2  # Only recent ones

    def test_recent_trades_all_old(self):
        s = MarketWhaleState(market_ticker="TEST")
        s.add_trade(_trade(side="yes", minutes_ago=60))
        s.add_trade(_trade(side="yes", minutes_ago=45))
        s.add_trade(_trade(side="yes", minutes_ago=35))
        assert len(s.recent_trades(30)) == 0

    def test_signal_emitted_flag(self):
        s = MarketWhaleState(market_ticker="TEST")
        assert not s.signal_emitted
        s.signal_emitted = True
        assert s.signal_emitted


# ── Watchlist ────────────────────────────────────────────────────────

class TestWatchlist:
    def test_add_new(self):
        w = Watchlist()
        mkt = WatchlistMarket(
            ticker="T1", event_ticker="E1",
            category="sports", title="Test", event_date=date.today(),
        )
        assert w.add(mkt) is True
        assert len(w.tickers) == 1

    def test_add_duplicate(self):
        w = Watchlist()
        mkt = WatchlistMarket(
            ticker="T1", event_ticker="E1",
            category="sports", title="Test", event_date=date.today(),
        )
        w.add(mkt)
        assert w.add(mkt) is False  # Duplicate
        assert len(w.tickers) == 1

    def test_get(self):
        w = Watchlist()
        mkt = WatchlistMarket(
            ticker="T1", event_ticker="E1",
            category="sports", title="Test", event_date=date.today(),
        )
        w.add(mkt)
        assert w.get("T1") is mkt
        assert w.get("NONEXISTENT") is None

    def test_remove(self):
        w = Watchlist()
        mkt = WatchlistMarket(
            ticker="T1", event_ticker="E1",
            category="sports", title="Test", event_date=date.today(),
        )
        w.add(mkt)
        w.remove("T1")
        assert len(w.tickers) == 0


# ── Signal scoring (WhaleDetector._maybe_emit_signal) ────────────────

class TestSignalScoring:
    """Test signal scoring logic by calling _maybe_emit_signal directly."""

    def _make_detector(self) -> WhaleDetector:
        client = MagicMock()
        config = WhaleConfig()
        watchlist = Watchlist()
        watchlist.add(WatchlistMarket(
            ticker="KXMLBGAME-26APR03-NYY",
            event_ticker="KXMLBGAME-26APR03",
            category="sports",
            title="Test Game",
            event_date=date(2026, 4, 3),
        ))
        signal_queue: asyncio.Queue[WhaleSignal] = asyncio.Queue()
        price_queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()
        tracker = MagicMock(spec=WhaleTracker)
        alerts = MagicMock()
        alerts._send = AsyncMock()

        detector = WhaleDetector(
            client=client,
            config=config,
            watchlist=watchlist,
            signal_queue=signal_queue,
            price_queue=price_queue,
            tracker=tracker,
            alerts=alerts,
        )
        return detector

    @pytest.mark.asyncio
    async def test_signal_fires_on_valid_criteria(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"

        # Set ask price in range
        d._asks[ticker] = Decimal("0.90")

        # Add 3 recent yes whale trades
        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", price="0.90", size="1200",
                minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)

        assert state.signal_emitted
        signal = d._signal_queue.get_nowait()
        assert signal.side == "yes"
        assert signal.whale_count == 3
        assert signal.consensus_pct == 1.0
        assert signal.best_ask == Decimal("0.90")

    @pytest.mark.asyncio
    async def test_no_signal_insufficient_whales(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.90")

        state = MarketWhaleState(market_ticker=ticker)
        # Only 2 trades (need 3)
        for i in range(2):
            state.add_trade(_trade(
                side="yes", minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted
        assert d._signal_queue.empty()

    @pytest.mark.asyncio
    async def test_no_signal_low_consensus(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.90")

        state = MarketWhaleState(market_ticker=ticker)
        # 2 yes, 2 no — 50% consensus (need 90%)
        state.add_trade(_trade(side="yes", minutes_ago=1, trade_id="t1", ticker=ticker))
        state.add_trade(_trade(side="yes", minutes_ago=2, trade_id="t2", ticker=ticker))
        state.add_trade(_trade(side="no", minutes_ago=3, trade_id="t3", ticker=ticker))
        state.add_trade(_trade(side="no", minutes_ago=4, trade_id="t4", ticker=ticker))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted  # Not locked — consensus could shift
        assert d._signal_queue.empty()
        d._tracker.log_signal_skip.assert_called()

    @pytest.mark.asyncio
    async def test_no_signal_price_too_low(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.50")  # Below 0.85 min

        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted  # Not locked — price could move into range
        assert d._signal_queue.empty()
        d._tracker.log_signal_skip.assert_called()

    @pytest.mark.asyncio
    async def test_no_signal_price_too_high(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.98")  # Above 0.95 max

        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted  # Not locked — price could drop into range
        assert d._signal_queue.empty()

    @pytest.mark.asyncio
    async def test_no_signal_no_ask_price(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        # Don't set d._asks[ticker] — no ask available

        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted

    @pytest.mark.asyncio
    async def test_no_signal_not_on_watchlist(self):
        d = self._make_detector()
        ticker = "KXUNKNOWN-26APR03-UNK"  # Not in watchlist
        d._asks[ticker] = Decimal("0.90")

        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted

    @pytest.mark.asyncio
    async def test_no_double_signal(self):
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.90")

        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        # First call emits
        await d._maybe_emit_signal(state)
        assert state.signal_emitted
        assert not d._signal_queue.empty()
        d._signal_queue.get_nowait()

        # Add more trades
        state.add_trade(_trade(
            side="yes", minutes_ago=0, trade_id="t-extra", ticker=ticker,
        ))

        # Second call should NOT emit again
        await d._maybe_emit_signal(state)
        assert d._signal_queue.empty()

    @pytest.mark.asyncio
    async def test_old_trades_dont_count(self):
        """Trades outside the 30-min window shouldn't trigger a signal."""
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.90")

        state = MarketWhaleState(market_ticker=ticker)
        # 3 old trades (35+ min ago, outside 30-min window)
        for i in range(3):
            state.add_trade(_trade(
                side="yes", minutes_ago=35 + i, trade_id=f"old{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert not state.signal_emitted

    @pytest.mark.asyncio
    async def test_no_consensus_side_for_signal(self):
        """YES consensus → entry at yes_ask. NO consensus → entry at 1-yes_ask."""
        d = self._make_detector()
        ticker = "KXMLBGAME-26APR03-NYY"
        d._asks[ticker] = Decimal("0.10")  # yes_ask = 0.10, so no_ask = 0.90

        state = MarketWhaleState(market_ticker=ticker)
        for i in range(3):
            state.add_trade(_trade(
                side="no", price="0.90", size="1200",
                minutes_ago=i, trade_id=f"t{i}", ticker=ticker,
            ))
        d._states[ticker] = state

        await d._maybe_emit_signal(state)
        assert state.signal_emitted
        signal = d._signal_queue.get_nowait()
        assert signal.side == "no"
        assert signal.best_ask == Decimal("0.90")  # 1 - 0.10


# ── Position monitor ────────────────────────────────────────────────

class TestPositionMonitor:
    def test_tracked_position_outcome(self):
        p = TrackedPosition(
            market_ticker="T1", side="yes",
            entry_price=Decimal("0.90"), size=10,
        )
        assert p.outcome == Outcome.YES

        p2 = TrackedPosition(
            market_ticker="T1", side="no",
            entry_price=Decimal("0.90"), size=10,
        )
        assert p2.outcome == Outcome.NO


# ── Balance tracking ────────────────────────────────────────────────

class TestBalanceTracking:
    def test_entry_deducts_cost_plus_fee(self):
        """Local balance should deduct price * contracts + fee on entry."""
        bal = {"balance": Decimal("100")}
        price = Decimal("0.90")
        contracts = 10
        fee = kalshi_fee(price, contracts)
        cost = price * contracts + fee

        bal["balance"] -= cost
        assert bal["balance"] == Decimal("100") - cost
        assert bal["balance"] > 0

    def test_balance_never_negative_from_sizing(self):
        """compute_size should never produce a size that costs more than balance."""
        bal = Decimal("100")
        price = Decimal("0.90")
        size = compute_size(price, bal)
        cost = price * size + kalshi_fee(price, size)
        assert cost <= bal

    def test_stop_loss_pnl_includes_exit_fee(self):
        """Stop loss P&L should account for exit fee."""
        entry_price = Decimal("0.90")
        fill_price = Decimal("0.77")  # After 15% drop
        contracts = 10
        exit_fee = kalshi_fee(fill_price, contracts)

        pnl = (fill_price - entry_price) * contracts - exit_fee
        assert pnl < 0  # Should be negative
        # Without fee: (0.77 - 0.90) * 10 = -1.30
        # With fee: -1.30 - fee
        assert pnl < Decimal("-1.30")

    def test_settlement_pnl_no_fee(self):
        """Settlement wins should NOT deduct any fee."""
        entry_price = Decimal("0.90")
        contracts = 10
        payout = Decimal(str(contracts))  # $1 per contract
        cost = entry_price * contracts
        pnl = payout - cost
        assert pnl == Decimal("1.00")  # No fee deducted


# ── Fill price computation (Bug 1 fix) ─────────────────────────────

class TestComputeFillPrice:
    """Verify _compute_fill_price returns correct price for YES and NO."""

    def test_yes_fill_dollars(self):
        """YES buy: cost/count = YES price directly."""
        result = {
            "taker_fill_cost_dollars": 62.98,  # 0.94 * 67
            "fill_count_fp": "67.00",
        }
        price = _compute_fill_price(result)
        assert price is not None
        assert abs(price - Decimal("0.94")) < Decimal("0.001")

    def test_no_fill_dollars(self):
        """NO buy: cost/count = NO price directly (NOT the YES price).

        This was the bug — the old code returned 1-price, double-inverting.
        A NO buy at NO_price=0.93 should return 0.93, not 0.07.
        """
        # 135 NO contracts at NO price $0.93 → cost = $125.55
        result = {
            "taker_fill_cost_dollars": 125.55,
            "fill_count_fp": "135.00",
        }
        price = _compute_fill_price(result)
        assert price is not None
        # Should be ~0.93 (NO price), NOT 0.07 (YES price)
        assert abs(price - Decimal("0.93")) < Decimal("0.001")
        assert price > Decimal("0.50")  # Sanity: definitely not the YES price

    def test_yes_fill_legacy_cents(self):
        """Legacy cent-denominated fields work for YES."""
        result = {
            "taker_fill_cost": 6298,  # 94 cents * 67 contracts
            "fill_count": 67,
        }
        price = _compute_fill_price(result)
        assert price is not None
        assert abs(price - Decimal("0.94")) < Decimal("0.001")

    def test_no_fill_legacy_cents(self):
        """Legacy cent-denominated fields work for NO."""
        result = {
            "taker_fill_cost": 12555,  # 93 cents * 135 contracts
            "fill_count": 135,
        }
        price = _compute_fill_price(result)
        assert price is not None
        assert abs(price - Decimal("0.93")) < Decimal("0.01")

    def test_zero_fill_count(self):
        result = {"taker_fill_cost_dollars": 100, "fill_count_fp": "0"}
        assert _compute_fill_price(result) is None

    def test_zero_cost(self):
        result = {"taker_fill_cost_dollars": 0, "fill_count_fp": "10.00"}
        assert _compute_fill_price(result) is None

    def test_no_fields(self):
        assert _compute_fill_price({}) is None


# ── Stop-loss tests ─────────────────────────────────────────────────

def _make_monitor(config=None, engine=None):
    """Create a PositionMonitor with mocked dependencies for testing."""
    config = config or WhaleConfig(stop_loss_pct=0.15)
    engine = engine or PaperExecutionEngine(
        initial_balance=Decimal("300"), balance_file=None,
    )
    client = AsyncMock()
    alerts = AsyncMock()
    tracker = MagicMock(spec=WhaleTracker)
    tracker.log_stop_loss = MagicMock()
    tracker.log_settlement = MagicMock()
    price_queue = asyncio.Queue()
    on_stop_loss = AsyncMock()

    monitor = PositionMonitor(
        config=config,
        engine=engine,
        client=client,
        alerts=alerts,
        tracker=tracker,
        price_queue=price_queue,
        on_stop_loss=on_stop_loss,
    )
    return monitor, engine, alerts, tracker, price_queue, on_stop_loss


async def _seed_position(monitor, engine, ticker="KXTEST-YES", side="yes",
                         entry_price=Decimal("0.90"), size=100):
    """Add a position to both monitor and paper engine."""
    outcome = Outcome.YES if side == "yes" else Outcome.NO
    monitor.add_position(TrackedPosition(
        market_ticker=ticker, side=side,
        entry_price=entry_price, size=size, order_id="test-order",
    ))
    await engine.place_order(OrderRequest(
        market_id=ticker, side=Side.BUY, outcome=outcome,
        price=entry_price, size=Decimal(str(size)),
    ))


class TestStopLossThreshold:
    """Stop-loss threshold calculation."""

    @pytest.mark.asyncio
    async def test_threshold_calculation(self):
        """Stop threshold is entry * (1 - stop_loss_pct)."""
        monitor, *_ = _make_monitor()
        pos = TrackedPosition(
            market_ticker="TEST", side="yes",
            entry_price=Decimal("0.90"), size=100,
        )
        assert monitor._stop_threshold(pos) == Decimal("0.765")

    @pytest.mark.asyncio
    async def test_threshold_custom_pct(self):
        """Custom stop_loss_pct is respected."""
        config = WhaleConfig(stop_loss_pct=0.10)
        monitor, *_ = _make_monitor(config=config)
        pos = TrackedPosition(
            market_ticker="TEST", side="yes",
            entry_price=Decimal("0.80"), size=50,
        )
        # 0.80 * (1 - 0.10) = 0.72
        assert monitor._stop_threshold(pos) == Decimal("0.72")


class TestStopLossDirectExecution:
    """Direct _execute_stop_loss calls — unit tests."""

    @pytest.mark.asyncio
    async def test_triggers_on_bid_drop(self):
        """Bid below threshold triggers sell, removes position, fires callbacks."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-YES"], trigger_bid=Decimal("0.75"),
        )

        assert monitor.open_count == 0
        assert not monitor.results.empty()
        ticker_result, pnl = await monitor.results.get()
        assert ticker_result == "KXTEST-YES"
        assert pnl < 0
        tracker.log_stop_loss.assert_called_once()
        alerts.whale_stop_loss.assert_awaited_once()
        on_stop_loss.assert_awaited_once_with("KXTEST-YES")

    @pytest.mark.asyncio
    async def test_pnl_accuracy(self):
        """P&L is computed correctly: (exit - entry) * size - fees."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        entry = Decimal("0.90")
        size = 100
        await _seed_position(monitor, engine, entry_price=entry, size=size)

        trigger = Decimal("0.75")
        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-YES"], trigger_bid=trigger,
        )

        _, pnl = await monitor.results.get()
        # Paper engine fills at trigger minus slippage (10bps = 0.001).
        # Fill price ≈ 0.749. P&L = (0.749 - 0.90) * 100 - entry_fee - exit_fee.
        # Just verify it's in the right ballpark: roughly -$15 ± fees.
        assert Decimal("-20") < pnl < Decimal("-10")

    @pytest.mark.asyncio
    async def test_hard_floor_prevents_sell(self):
        """Bid at or below $0.05 does NOT trigger sell."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-YES"], trigger_bid=Decimal("0.03"),
        )

        assert monitor.open_count == 1
        tracker.log_stop_loss.assert_not_called()
        assert monitor.results.empty()

    @pytest.mark.asyncio
    async def test_hard_floor_boundary(self):
        """Bid at exactly $0.05 does NOT trigger sell."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-YES"], trigger_bid=Decimal("0.05"),
        )

        assert monitor.open_count == 1

    @pytest.mark.asyncio
    async def test_no_side_uses_no_bid(self):
        """NO-side position triggers on no_bid, not yes_bid."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(
            monitor, engine, ticker="KXTEST-NO", side="no",
            entry_price=Decimal("0.90"), size=50,
        )

        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-NO"], trigger_bid=Decimal("0.70"),
        )
        assert monitor.open_count == 0
        on_stop_loss.assert_awaited_once_with("KXTEST-NO")

    @pytest.mark.asyncio
    async def test_race_condition_double_process(self):
        """If stop-loss already removed position, second call is a no-op."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        pos = monitor._positions["KXTEST-YES"]
        await monitor._execute_stop_loss(pos, trigger_bid=Decimal("0.70"))
        assert monitor.open_count == 0

        # Second call with same pos — should be a no-op.
        await monitor._execute_stop_loss(pos, trigger_bid=Decimal("0.70"))
        # Still only one result on queue, one callback.
        assert monitor.results.qsize() == 1
        on_stop_loss.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_fill(self):
        """Partial fill reduces position size but keeps it open."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()

        # Use a mock engine that returns partial fills.
        mock_engine = AsyncMock()
        mock_engine.place_order.return_value = MagicMock(
            filled_size=Decimal("60"), avg_fill_price=Decimal("0.74"),
            order_id="partial-order", status=MagicMock(value="filled"),
        )
        mock_engine.get_balance.return_value = Decimal("200")
        mock_engine.get_positions.return_value = []
        monitor._engine = mock_engine

        monitor.add_position(TrackedPosition(
            market_ticker="KXTEST-PARTIAL", side="yes",
            entry_price=Decimal("0.90"), size=100, order_id="test",
        ))

        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-PARTIAL"], trigger_bid=Decimal("0.74"),
        )

        # Position should still exist with reduced size.
        assert monitor.open_count == 1
        assert monitor._positions["KXTEST-PARTIAL"].size == 40
        # Result pushed (for the filled portion).
        assert not monitor.results.empty()
        # Callback NOT fired (not fully exited).
        on_stop_loss.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_fills_retries(self):
        """Zero fills on sell does NOT remove position — retries next tick."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()

        mock_engine = AsyncMock()
        mock_engine.place_order.return_value = MagicMock(
            filled_size=Decimal("0"), avg_fill_price=None,
            order_id="no-fill", status=MagicMock(value="cancelled"),
        )
        monitor._engine = mock_engine

        monitor.add_position(TrackedPosition(
            market_ticker="KXTEST-NOFILL", side="yes",
            entry_price=Decimal("0.90"), size=100, order_id="test",
        ))

        await monitor._execute_stop_loss(
            monitor._positions["KXTEST-NOFILL"], trigger_bid=Decimal("0.70"),
        )

        # Position still open — will retry on next price tick.
        assert monitor.open_count == 1
        assert monitor.results.empty()
        tracker.log_stop_loss.assert_not_called()


class TestStopLossLoop:
    """End-to-end: run_price_monitor loop processes queue and triggers stops.

    Uses asyncio.run() wrapper to avoid pytest-asyncio event loop issues
    with create_task in older versions.
    """

    def _run(self, coro):
        """Run an async test in a fresh event loop."""
        return asyncio.get_event_loop().run_until_complete(coro)

    @pytest.mark.asyncio
    async def test_loop_triggers_stop_on_low_bid(self):
        """run_price_monitor processes PriceUpdates and fires stop loss."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        async def _run_loop():
            # Feed a sequence: safe bid, then dangerous bid.
            await queue.put(PriceUpdate(
                market_id="KXTEST-YES", yes_bid=Decimal("0.85"),
            ))
            await queue.put(PriceUpdate(
                market_id="KXTEST-YES", yes_bid=Decimal("0.75"),
            ))

            task = asyncio.ensure_future(monitor.run_price_monitor())
            try:
                return await asyncio.wait_for(
                    monitor.results.get(), timeout=3.0,
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        ticker_result, pnl = await _run_loop()

        assert ticker_result == "KXTEST-YES"
        assert pnl < 0
        assert monitor.open_count == 0
        tracker.log_stop_loss.assert_called_once()

    @pytest.mark.asyncio
    async def test_loop_ignores_safe_bids(self):
        """Bids above threshold are processed but don't trigger stop."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        # All bids above stop threshold of $0.765.
        for bid in ["0.85", "0.80", "0.77"]:
            await queue.put(PriceUpdate(
                market_id="KXTEST-YES", yes_bid=Decimal(bid),
            ))

        async def _drain():
            task = asyncio.ensure_future(monitor.run_price_monitor())
            # Wait until queue is drained.
            while not queue.empty():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)  # Let last iteration finish.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await _drain()

        assert monitor.open_count == 1
        assert monitor.results.empty()
        tracker.log_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_ignores_untracked_tickers(self):
        """Price updates for markets we don't hold are ignored."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        await queue.put(PriceUpdate(
            market_id="KXOTHER-YES", yes_bid=Decimal("0.10"),
        ))

        async def _drain():
            task = asyncio.ensure_future(monitor.run_price_monitor())
            while not queue.empty():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await _drain()

        assert monitor.open_count == 1
        tracker.log_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_no_side_position(self):
        """Loop correctly uses no_bid for NO-side positions."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(
            monitor, engine, ticker="KXTEST-NO", side="no",
            entry_price=Decimal("0.90"), size=50,
        )

        await queue.put(PriceUpdate(
            market_id="KXTEST-NO", yes_bid=Decimal("0.10"),
            no_bid=Decimal("0.70"),
        ))

        async def _run_loop():
            task = asyncio.ensure_future(monitor.run_price_monitor())
            try:
                return await asyncio.wait_for(
                    monitor.results.get(), timeout=3.0,
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        ticker_result, pnl = await _run_loop()

        assert ticker_result == "KXTEST-NO"
        assert monitor.open_count == 0


class TestStopLossRESTFallback:
    """REST fallback for stale WS prices."""

    @pytest.mark.asyncio
    async def test_rest_fallback_triggers_stop(self):
        """If WS is stale, REST fetch triggers stop-loss."""
        import time as _time

        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        # Fake the last price time to be old (>60s ago).
        monitor._last_price_time["KXTEST-YES"] = _time.monotonic() - 120.0

        # Mock the client to return a low bid.
        monitor._client.fetch_market.return_value = {
            "yes_bid_dollars": "0.70",
            "result": "",
        }

        await monitor._check_stale_prices()

        assert monitor.open_count == 0
        assert not monitor.results.empty()
        _, pnl = await monitor.results.get()
        assert pnl < 0

    @pytest.mark.asyncio
    async def test_rest_fallback_skips_fresh(self):
        """REST fallback does NOT fire if WS data is fresh."""
        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        # last_price_time is fresh (set by add_position).
        monitor._client.fetch_market.return_value = {
            "yes_bid_dollars": "0.70",
        }

        await monitor._check_stale_prices()

        # Position should still be open — WS data was fresh, REST not triggered.
        assert monitor.open_count == 1
        monitor._client.fetch_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_rest_fallback_safe_bid(self):
        """REST fallback fetches but bid is above threshold — no stop."""
        import time as _time

        monitor, engine, alerts, tracker, queue, on_stop_loss = _make_monitor()
        await _seed_position(monitor, engine)

        monitor._last_price_time["KXTEST-YES"] = _time.monotonic() - 120.0

        monitor._client.fetch_market.return_value = {
            "yes_bid_dollars": "0.85",
            "result": "",
        }

        await monitor._check_stale_prices()

        assert monitor.open_count == 1
        assert monitor.results.empty()


class TestStopLossPaperDryRun:
    """Paper trading integration: buy → stop loss → verify balance."""

    @pytest.mark.asyncio
    async def test_full_cycle_buy_then_stop(self):
        """Buy 100 YES at $0.90, stop at $0.75 — verify balance."""
        engine = PaperExecutionEngine(
            initial_balance=Decimal("300"), balance_file=None,
        )
        monitor, _, alerts, tracker, queue, on_stop_loss = _make_monitor(
            engine=engine,
        )

        entry_price = Decimal("0.90")
        size = 100
        buy_resp = await engine.place_order(OrderRequest(
            market_id="GAME-A", side=Side.BUY, outcome=Outcome.YES,
            price=entry_price, size=Decimal(str(size)),
        ))
        balance_after_buy = await engine.get_balance()
        assert balance_after_buy < Decimal("300")

        monitor.add_position(TrackedPosition(
            market_ticker="GAME-A", side="yes",
            entry_price=entry_price, size=size,
            order_id=buy_resp.order_id,
        ))

        # Stop loss at $0.75 via the loop.
        await queue.put(PriceUpdate(
            market_id="GAME-A", yes_bid=Decimal("0.75"),
        ))

        async def _run_loop():
            task = asyncio.ensure_future(monitor.run_price_monitor())
            try:
                return await asyncio.wait_for(
                    monitor.results.get(), timeout=3.0,
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        ticker_result, pnl = await _run_loop()

        assert ticker_result == "GAME-A"
        assert pnl < 0
        assert monitor.open_count == 0

        final_balance = await engine.get_balance()
        assert final_balance > Decimal("0")
        assert final_balance < Decimal("300")
        # Lost roughly (0.90 - 0.75) * 100 + fees ≈ $15 + fees.
        assert Decimal("270") < final_balance < Decimal("295")

    @pytest.mark.asyncio
    async def test_two_positions_one_stops(self):
        """Two concurrent positions, one stops, the other stays open."""
        engine = PaperExecutionEngine(
            initial_balance=Decimal("300"), balance_file=None,
        )
        monitor, _, alerts, tracker, queue, on_stop_loss = _make_monitor(
            engine=engine,
        )

        await engine.place_order(OrderRequest(
            market_id="GAME-A", side=Side.BUY, outcome=Outcome.YES,
            price=Decimal("0.90"), size=Decimal("50"),
        ))
        monitor.add_position(TrackedPosition(
            market_ticker="GAME-A", side="yes",
            entry_price=Decimal("0.90"), size=50, order_id="a",
        ))

        await engine.place_order(OrderRequest(
            market_id="GAME-B", side=Side.BUY, outcome=Outcome.YES,
            price=Decimal("0.85"), size=Decimal("50"),
        ))
        monitor.add_position(TrackedPosition(
            market_ticker="GAME-B", side="yes",
            entry_price=Decimal("0.85"), size=50, order_id="b",
        ))

        assert monitor.open_count == 2

        # GAME-B safe bid first, then GAME-A triggers stop.
        await queue.put(PriceUpdate(
            market_id="GAME-B", yes_bid=Decimal("0.80"),
        ))
        await queue.put(PriceUpdate(
            market_id="GAME-A", yes_bid=Decimal("0.70"),
        ))

        async def _run_loop():
            task = asyncio.ensure_future(monitor.run_price_monitor())
            try:
                return await asyncio.wait_for(
                    monitor.results.get(), timeout=3.0,
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        ticker_result, pnl = await _run_loop()

        assert ticker_result == "GAME-A"
        assert pnl < 0
        assert monitor.open_count == 1
        assert "GAME-B" in monitor._positions
        assert "GAME-A" not in monitor._positions
