# Event tracking. CSV log + file log for full lifecycle observability.

"""Whale bot event tracker — logs every moment in the trade lifecycle.

Three tiers:
    CSV  — structured data for post-analysis (data/trades/kalshi-whale-YYYY-MM-DD.csv)
    File — operational log (data/alerts/YYYY-MM-DD.log via AlertManager)
    TG   — Telegram alerts for actionable moments only

Event types logged to CSV:
    WATCHLIST    — discovery cycle results (markets subscribed)
    WHALE_TRADE  — individual whale trade detected
    SIGNAL_PASS  — signal criteria met, proceeding to entry
    SIGNAL_SKIP  — signal criteria checked but failed (with reason)
    ENTRY        — order placed (ideal vs actual price, slippage, fees)
    STOP_LOSS    — stop loss triggered (trigger price, fill, slippage)
    SETTLEMENT   — market resolved (outcome, P&L)
    ROUND_SUMMARY — periodic stats
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bots.kalshi_whale.sizing import kalshi_fee

logger = logging.getLogger(__name__)


HEADERS = [
    "timestamp",
    "event_type",
    "market_ticker",
    # Whale trade fields
    "trade_id",
    "taker_side",
    "trade_price",
    "trade_size",
    "trade_notional",
    # Signal fields
    "whale_count",
    "consensus_pct",
    "consensus_side",
    "total_whale_volume",
    "skip_reason",
    # Entry/exit fields
    "side",
    "outcome",
    "ideal_price",
    "actual_price",
    "slippage_cents",
    "contracts",
    "ideal_fee",
    "actual_fee",
    "ideal_cost",
    "actual_cost",
    "order_id",
    "order_status",
    # Position/P&L fields
    "entry_price",
    "stop_price",
    "trigger_price",
    "pnl",
    "balance_after",
    # Watchlist fields
    "watchlist_size",
    "markets_added",
]


class WhaleTracker:
    """Logs every event in the whale bot lifecycle to CSV.

    One CSV per day. All fields are optional per event type —
    unused fields are left empty.
    """

    def __init__(self, data_dir: str = "data/trades") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._writer: csv.writer | None = None
        self._file = None

    def _ensure_file(self) -> csv.writer:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self.close()
            self._current_date = today
            path = self._data_dir / f"kalshi-whale-{today}.csv"
            is_new = not path.exists()
            self._file = open(path, "a", newline="")
            self._writer = csv.writer(self._file)
            if is_new:
                self._writer.writerow(HEADERS)
                logger.info("Tracker: created %s", path)
        assert self._writer is not None
        return self._writer

    def _write(self, row: dict) -> None:
        writer = self._ensure_file()
        row["timestamp"] = datetime.now(timezone.utc).isoformat()
        writer.writerow([row.get(h, "") for h in HEADERS])
        if self._file:
            self._file.flush()

    # ---- Watchlist ----

    def log_watchlist(self, watchlist_size: int, markets_added: int) -> None:
        self._write({
            "event_type": "WATCHLIST",
            "watchlist_size": watchlist_size,
            "markets_added": markets_added,
        })

    # ---- Whale trades ----

    def log_whale_trade(
        self,
        market_ticker: str,
        trade_id: str,
        taker_side: str,
        price: Decimal,
        size: Decimal,
        notional: Decimal,
        whale_count: int,
        consensus_pct: float,
        consensus_side: str | None,
    ) -> None:
        self._write({
            "event_type": "WHALE_TRADE",
            "market_ticker": market_ticker,
            "trade_id": trade_id,
            "taker_side": taker_side,
            "trade_price": str(price),
            "trade_size": str(size),
            "trade_notional": str(notional),
            "whale_count": whale_count,
            "consensus_pct": f"{consensus_pct:.4f}",
            "consensus_side": consensus_side or "",
        })

    # ---- Signal scoring ----

    def log_signal_pass(
        self,
        market_ticker: str,
        side: str,
        whale_count: int,
        consensus_pct: float,
        total_volume: Decimal,
        best_ask: Decimal,
    ) -> None:
        self._write({
            "event_type": "SIGNAL_PASS",
            "market_ticker": market_ticker,
            "consensus_side": side,
            "whale_count": whale_count,
            "consensus_pct": f"{consensus_pct:.4f}",
            "total_whale_volume": str(total_volume),
            "ideal_price": str(best_ask),
        })

    def log_signal_skip(
        self,
        market_ticker: str,
        reason: str,
        whale_count: int = 0,
        consensus_pct: float = 0.0,
        consensus_side: str = "",
    ) -> None:
        self._write({
            "event_type": "SIGNAL_SKIP",
            "market_ticker": market_ticker,
            "skip_reason": reason,
            "whale_count": whale_count,
            "consensus_pct": f"{consensus_pct:.4f}",
            "consensus_side": consensus_side,
        })

    # ---- Entry ----

    def log_entry(
        self,
        market_ticker: str,
        side: str,
        outcome: str,
        ideal_price: Decimal,
        actual_price: Decimal | None,
        contracts: int,
        order_id: str,
        order_status: str,
        balance_after: Decimal | None = None,
    ) -> None:
        slippage = ""
        actual_fee = ""
        actual_cost = ""
        if actual_price is not None:
            slippage = str((actual_price - ideal_price) * 100)
            af = kalshi_fee(actual_price, contracts)
            actual_fee = str(af)
            actual_cost = str(actual_price * contracts + af)

        ideal_f = kalshi_fee(ideal_price, contracts)

        self._write({
            "event_type": "ENTRY",
            "market_ticker": market_ticker,
            "side": side,
            "outcome": outcome,
            "ideal_price": str(ideal_price),
            "actual_price": str(actual_price) if actual_price else "",
            "slippage_cents": slippage,
            "contracts": contracts,
            "ideal_fee": str(ideal_f),
            "actual_fee": actual_fee,
            "ideal_cost": str(ideal_price * contracts + ideal_f),
            "actual_cost": actual_cost,
            "order_id": order_id,
            "order_status": order_status,
            "balance_after": str(balance_after) if balance_after else "",
        })

    # ---- Stop loss ----

    def log_stop_loss(
        self,
        market_ticker: str,
        side: str,
        outcome: str,
        entry_price: Decimal,
        stop_price: Decimal,
        trigger_price: Decimal,
        actual_price: Decimal | None,
        contracts: int,
        order_id: str,
        order_status: str,
        pnl: Decimal,
        balance_after: Decimal | None = None,
    ) -> None:
        slippage = ""
        actual_fee = ""
        ideal_fee = ""
        if actual_price is not None:
            slippage = str((trigger_price - actual_price) * 100)
            actual_fee = str(kalshi_fee(actual_price, contracts))
        if trigger_price > 0:
            ideal_fee = str(kalshi_fee(trigger_price, contracts))

        self._write({
            "event_type": "STOP_LOSS",
            "market_ticker": market_ticker,
            "side": side,
            "outcome": outcome,
            "entry_price": str(entry_price),
            "stop_price": str(stop_price),
            "trigger_price": str(trigger_price),
            "ideal_price": str(trigger_price),
            "actual_price": str(actual_price) if actual_price else "",
            "slippage_cents": slippage,
            "contracts": contracts,
            "ideal_fee": ideal_fee,
            "actual_fee": actual_fee,
            "order_id": order_id,
            "order_status": order_status,
            "pnl": str(pnl),
            "balance_after": str(balance_after) if balance_after else "",
        })

    # ---- Settlement ----

    def log_settlement(
        self,
        market_ticker: str,
        side: str,
        outcome: str,
        entry_price: Decimal,
        contracts: int,
        pnl: Decimal,
        balance_after: Decimal | None = None,
    ) -> None:
        self._write({
            "event_type": "SETTLEMENT",
            "market_ticker": market_ticker,
            "side": side,
            "outcome": outcome,
            "entry_price": str(entry_price),
            "contracts": contracts,
            "pnl": str(pnl),
            "balance_after": str(balance_after) if balance_after else "",
        })

    # ---- Summary ----

    def log_summary(
        self,
        watchlist_size: int,
        signals: int,
        trades: int,
        wins: int,
        pnl: Decimal,
        balance: Decimal,
    ) -> None:
        self._write({
            "event_type": "ROUND_SUMMARY",
            "watchlist_size": watchlist_size,
            "whale_count": signals,
            "contracts": trades,
            "consensus_pct": f"{wins / trades * 100:.1f}" if trades > 0 else "",
            "pnl": str(pnl),
            "balance_after": str(balance),
        })

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None
