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
from shared.types import Outcome, PriceUpdate


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
