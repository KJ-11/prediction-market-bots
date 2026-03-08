"""CSV trade logger — logs every signal and execution for post-run analysis."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)


class TradeLog:
    """Appends trade signals and execution results to a CSV file.

    Creates one CSV per bot per day: data/trades/kalshi-crypto-KXBTC15M-2026-03-04.csv
    """

    HEADERS = [
        "timestamp",
        "round_ticker",
        "strategy",
        "side",
        "outcome",
        "price",
        "size",
        "confidence",
        "reason",
        "order_id",
        "status",
        "fill_price",
        "fill_size",
        "balance_after",
    ]

    def __init__(self, bot_name: str, data_dir: str = "data/trades") -> None:
        self._bot_name = bot_name
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._writer: csv.writer | None = None
        self._file = None

    def _ensure_file(self) -> csv.writer:
        """Open or rotate to today's CSV file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._close()
            self._current_date = today
            path = self._data_dir / f"{self._bot_name}-{today}.csv"
            is_new = not path.exists()
            self._file = open(path, "a", newline="")
            self._writer = csv.writer(self._file)
            if is_new:
                self._writer.writerow(self.HEADERS)
                logger.info("Trade log: created %s", path)
        assert self._writer is not None
        return self._writer

    def log_signal(
        self,
        round_ticker: str,
        strategy: str,
        side: str,
        outcome: str,
        price: Decimal,
        size: Decimal,
        confidence: float,
        reason: str,
        order_id: str = "",
        status: str = "",
        fill_price: Decimal | None = None,
        fill_size: Decimal | None = None,
        balance_after: Decimal | None = None,
    ) -> None:
        """Log a trade signal and its execution result."""
        writer = self._ensure_file()
        ts = datetime.now(timezone.utc).isoformat()
        writer.writerow([
            ts,
            round_ticker,
            strategy,
            side,
            outcome,
            str(price),
            str(size),
            f"{confidence:.4f}",
            reason,
            order_id,
            status,
            str(fill_price) if fill_price is not None else "",
            str(fill_size) if fill_size is not None else "",
            str(balance_after) if balance_after is not None else "",
        ])
        if self._file:
            self._file.flush()

    def log_round_summary(
        self,
        round_ticker: str,
        signals_generated: int,
        trades_executed: int,
        balance: Decimal,
    ) -> None:
        """Log a round summary line."""
        writer = self._ensure_file()
        ts = datetime.now(timezone.utc).isoformat()
        writer.writerow([
            ts,
            round_ticker,
            "ROUND_SUMMARY",
            "",
            "",
            "",
            "",
            "",
            f"signals={signals_generated} trades={trades_executed}",
            "",
            "",
            "",
            "",
            str(balance),
        ])
        if self._file:
            self._file.flush()

    def _close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None

    def close(self) -> None:
        """Close the log file."""
        self._close()
