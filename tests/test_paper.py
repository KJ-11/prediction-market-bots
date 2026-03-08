"""Tests for shared/execution/paper.py — PaperExecutionEngine."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from shared.execution.paper import PaperExecutionEngine
from shared.types import OrderRequest, Outcome, Side


def _buy_order(price="0.90", size="10") -> OrderRequest:
    return OrderRequest(
        market_id="TEST-MKT",
        side=Side.BUY,
        outcome=Outcome.YES,
        price=Decimal(price),
        size=Decimal(size),
    )


class TestPaperPersistence:
    @pytest.mark.asyncio
    async def test_balance_saved_after_trade(self, tmp_path):
        bf = tmp_path / "balance.json"
        engine = PaperExecutionEngine(
            initial_balance=Decimal("50"), balance_file=bf
        )
        await engine.place_order(_buy_order(price="0.90", size="1"))

        data = json.loads(bf.read_text())
        assert Decimal(data["balance"]) < Decimal("50")

    @pytest.mark.asyncio
    async def test_balance_loaded_on_restart(self, tmp_path):
        bf = tmp_path / "balance.json"
        bf.write_text(json.dumps({"balance": "123.45"}) + "\n")

        engine = PaperExecutionEngine(
            initial_balance=Decimal("50"), balance_file=bf
        )
        bal = await engine.get_balance()
        assert bal == Decimal("123.45")

    @pytest.mark.asyncio
    async def test_fresh_start_if_no_file(self, tmp_path):
        bf = tmp_path / "nonexistent" / "balance.json"
        engine = PaperExecutionEngine(
            initial_balance=Decimal("50"), balance_file=bf
        )
        bal = await engine.get_balance()
        assert bal == Decimal("50")

    @pytest.mark.asyncio
    async def test_fresh_start_if_corrupt_file(self, tmp_path):
        bf = tmp_path / "balance.json"
        bf.write_text("not json{{{")

        engine = PaperExecutionEngine(
            initial_balance=Decimal("50"), balance_file=bf
        )
        bal = await engine.get_balance()
        assert bal == Decimal("50")

    @pytest.mark.asyncio
    async def test_no_file_written_when_disabled(self, tmp_path):
        engine = PaperExecutionEngine(
            initial_balance=Decimal("50"), balance_file=None
        )
        await engine.place_order(_buy_order(price="0.90", size="1"))
        assert not list(tmp_path.iterdir())

    @pytest.mark.asyncio
    async def test_balance_updated_after_settlement(self, tmp_path):
        bf = tmp_path / "balance.json"
        engine = PaperExecutionEngine(
            initial_balance=Decimal("50"), balance_file=bf
        )
        await engine.place_order(_buy_order(price="0.90", size="5"))
        await engine.settle_market("TEST-MKT", Outcome.YES)

        data = json.loads(bf.read_text())
        # Won: paid ~0.90*5, got back $1*5, balance should be > 50
        assert Decimal(data["balance"]) > Decimal("50")
