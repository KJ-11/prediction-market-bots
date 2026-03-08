"""Tests for shared/risk.py — KillSwitch and RiskLimits."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shared.execution.paper import PaperExecutionEngine
from shared.risk import CircuitBreaker, KillSwitch, KillSwitchTriggered, RiskLimits
from shared.types import OrderRequest, Outcome, Side

# ---- KillSwitch tests ----


class TestKillSwitch:
    def test_no_trigger_on_healthy_state(self):
        ks = KillSwitch(
            kill_file="/tmp/nonexistent-kill-file-xyz",
            max_loss_pct=20.0,
            max_consecutive_errors=5,
            initial_balance=Decimal("500"),
        )
        ks.check(Decimal("490"))  # 2% loss — fine

    def test_loss_trigger(self):
        ks = KillSwitch(
            kill_file="/tmp/nonexistent-kill-file-xyz",
            max_loss_pct=20.0,
            max_consecutive_errors=10,
            initial_balance=Decimal("500"),
        )
        with pytest.raises(KillSwitchTriggered, match="Capital loss"):
            ks.check(Decimal("350"))  # 30% loss

    def test_error_trigger(self):
        ks = KillSwitch(
            kill_file="/tmp/nonexistent-kill-file-xyz",
            max_loss_pct=50.0,
            max_consecutive_errors=3,
            initial_balance=Decimal("500"),
        )
        ks.record_error()
        ks.record_error()
        ks.check(Decimal("500"))  # 2 errors — fine
        ks.record_error()
        with pytest.raises(KillSwitchTriggered, match="consecutive errors"):
            ks.check(Decimal("500"))

    def test_clear_errors(self):
        ks = KillSwitch(
            kill_file="/tmp/nonexistent-kill-file-xyz",
            max_loss_pct=50.0,
            max_consecutive_errors=3,
            initial_balance=Decimal("500"),
        )
        ks.record_error()
        ks.record_error()
        ks.clear_errors()
        ks.record_error()
        ks.check(Decimal("500"))  # Only 1 error after clear

    def test_file_trigger(self, tmp_path):
        kill_file = tmp_path / "kill"
        kill_file.touch()
        ks = KillSwitch(
            kill_file=str(kill_file),
            max_loss_pct=50.0,
            max_consecutive_errors=10,
            initial_balance=Decimal("500"),
        )
        with pytest.raises(KillSwitchTriggered, match="Kill file"):
            ks.check(Decimal("500"))

    def test_disabled(self, tmp_path):
        kill_file = tmp_path / "kill"
        kill_file.touch()
        ks = KillSwitch(
            kill_file=str(kill_file),
            max_loss_pct=1.0,
            max_consecutive_errors=1,
            initial_balance=Decimal("500"),
        )
        ks.disable()
        ks.record_error()
        ks.check(Decimal("100"))  # All triggers should fire, but disabled

    def test_no_balance_check_when_none(self):
        ks = KillSwitch(
            kill_file="/tmp/nonexistent-kill-file-xyz",
            max_loss_pct=1.0,
            max_consecutive_errors=10,
            initial_balance=Decimal("500"),
        )
        ks.check(None)  # Should not raise


# ---- RiskLimits tests ----


class TestRiskLimits:
    @pytest.fixture
    def engine(self):
        return PaperExecutionEngine(initial_balance=Decimal("500"), balance_file=None)

    @pytest.fixture
    def risk(self):
        return RiskLimits(
            max_position_pct=50.0,
            max_exposure_pct=80.0,
            max_loss_per_trade_pct=30.0,
            max_orders_per_min=30,
        )

    def _order(self, price="0.50", size="10") -> OrderRequest:
        return OrderRequest(
            market_id="TEST-MARKET",
            side=Side.BUY,
            outcome=Outcome.YES,
            price=Decimal(price),
            size=Decimal(size),
        )

    @pytest.mark.asyncio
    async def test_allowed_order(self, risk, engine):
        order = self._order(price="0.40", size="10")  # $4 = 0.8%
        result = await risk.check(order, engine)
        assert result.allowed

    @pytest.mark.asyncio
    async def test_blocked_by_trade_loss(self, risk, engine):
        # $300 = 60% of $500 balance, limit is 30%
        order = self._order(price="0.60", size="500")
        result = await risk.check(order, engine)
        assert not result.allowed
        assert "Trade" in result.reason

    @pytest.mark.asyncio
    async def test_blocked_by_position_size(self, risk, engine):
        # $300 = 60% of $500 balance, limit is 50%
        order = self._order(price="0.50", size="600")
        result = await risk.check(order, engine)
        assert not result.allowed

    @pytest.mark.asyncio
    async def test_blocked_by_exposure(self):
        # $100 balance, place $40 exposure, then try $40 more
        engine = PaperExecutionEngine(initial_balance=Decimal("100"), balance_file=None)
        risk = RiskLimits(
            max_position_pct=90.0,
            max_exposure_pct=50.0,  # 50% limit
            max_loss_per_trade_pct=90.0,
        )
        # Place order: $0.50 × 80 = $40 exposure
        await engine.place_order(self._order(price="0.50", size="80"))
        # Try another $40 — total $80 would be >50% of remaining bal
        order = self._order(price="0.50", size="80")
        result = await risk.check(order, engine)
        assert not result.allowed
        assert "Exposure" in result.reason


# ---- CircuitBreaker tests ----


class TestCircuitBreaker:
    def test_no_skip_initially(self):
        cb = CircuitBreaker(max_consecutive_losses=3)
        cb.set_day_start_balance(Decimal("50"))
        assert not cb.should_skip_round
        assert not cb.stopped_for_day

    def test_skip_after_consecutive_losses(self):
        cb = CircuitBreaker(max_consecutive_losses=3)
        cb.set_day_start_balance(Decimal("50"))

        cb.record_round_result(won=False, current_balance=Decimal("48"))
        cb.record_round_result(won=False, current_balance=Decimal("46"))
        assert not cb.should_skip_round

        cb.record_round_result(won=False, current_balance=Decimal("44"))
        assert cb.should_skip_round

    def test_win_resets_consecutive_losses(self):
        cb = CircuitBreaker(max_consecutive_losses=3)
        cb.set_day_start_balance(Decimal("50"))

        cb.record_round_result(won=False, current_balance=Decimal("48"))
        cb.record_round_result(won=False, current_balance=Decimal("46"))
        cb.record_round_result(won=True, current_balance=Decimal("47"))
        cb.record_round_result(won=False, current_balance=Decimal("45"))
        assert not cb.should_skip_round

    def test_clear_skip(self):
        cb = CircuitBreaker(max_consecutive_losses=1)
        cb.set_day_start_balance(Decimal("50"))
        cb.record_round_result(won=False, current_balance=Decimal("48"))
        assert cb.should_skip_round
        cb.clear_skip()
        assert not cb.should_skip_round

    def test_daily_loss_stops_trading(self):
        cb = CircuitBreaker(daily_loss_limit_pct=20.0)
        cb.set_day_start_balance(Decimal("50"))

        cb.check(Decimal("45"))  # 10% loss — fine
        assert not cb.stopped_for_day

        cb.check(Decimal("39"))  # 22% loss — stopped
        assert cb.stopped_for_day

    def test_drawdown_triggers_kill_switch(self):
        # Set daily loss limit high so it doesn't interfere with drawdown test
        cb = CircuitBreaker(max_drawdown_pct=40.0, daily_loss_limit_pct=50.0)
        cb.set_day_start_balance(Decimal("100"))

        # ATH is 100
        cb.check(Decimal("70"))  # 30% drawdown — ok
        assert not cb.stopped_for_day

        with pytest.raises(KillSwitchTriggered, match="Drawdown"):
            cb.check(Decimal("55"))  # 45% drawdown — kill

    def test_ath_updates(self):
        cb = CircuitBreaker(max_drawdown_pct=40.0)
        cb.set_day_start_balance(Decimal("50"))

        # Balance grows
        cb.record_round_result(won=True, current_balance=Decimal("60"))
        cb.record_round_result(won=True, current_balance=Decimal("70"))

        # Now ATH is 70, so 40% drawdown = $42
        cb.check(Decimal("45"))  # 35.7% — ok

        with pytest.raises(KillSwitchTriggered, match="Drawdown"):
            cb.check(Decimal("40"))  # 42.9% — kill
