"""Tests for Polymarket collection infrastructure.

Tests: slug construction, token extraction, CSV writing, round logic.
"""

from __future__ import annotations

import csv
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from scripts.collect_polymarket import (
    CSV_HEADERS,
    DURATIONS,
    RoundCollector,
    _extract_tokens,
    _seconds_until,
)
from shared.clients.polymarket import PolymarketClient


class TestSlugConstruction:
    def test_slug_format(self):
        """Slug should be {coin}-updown-{duration}-{unix_timestamp}."""
        interval = 300  # 5m
        now = int(time.time())
        window_end = ((now // interval) + 1) * interval
        slug = f"btc-updown-5m-{window_end}"
        assert slug.startswith("btc-updown-5m-")
        assert window_end % interval == 0

    def test_durations_match_client(self):
        assert DURATIONS == PolymarketClient.CRYPTO_DURATIONS


class TestExtractTokens:
    def test_clob_string_format(self):
        market = {"clobTokenIds": '["token_up_123", "token_down_456"]'}
        up, down = _extract_tokens(market)
        assert up == "token_up_123"
        assert down == "token_down_456"

    def test_tokens_list_fallback(self):
        market = {"tokens": [{"token_id": "up_abc"}, {"token_id": "down_def"}]}
        up, down = _extract_tokens(market)
        assert up == "up_abc"
        assert down == "down_def"

    def test_empty_market(self):
        up, down = _extract_tokens({})
        assert up == ""
        assert down == ""


class TestSecondsUntil:
    def test_future_timestamp(self):
        result = _seconds_until("2099-01-01T00:00:00Z")
        assert result > 0

    def test_empty_string(self):
        assert _seconds_until("") == -1.0

    def test_invalid_string(self):
        assert _seconds_until("not-a-date") == -1.0


class TestRoundCollector:
    def test_snapshot_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = RoundCollector("btc", "5m", data_dir=tmpdir)
            collector.write_snapshot(
                slug="btc-updown-5m-123", condition_id="0xabc",
                end_date="2026-01-01T00:05:00Z", seconds_remaining=120.0,
                up_token_id="up123", down_token_id="down456",
                up_bid=Decimal("0.45"), up_ask=Decimal("0.55"),
                down_bid=Decimal("0.45"), down_ask=Decimal("0.55"),
                last_trade_price=None, last_trade_side="",
                spot_price=Decimal("71000"), kraken_price=Decimal("71002"),
                rtds_price=Decimal("71005"), volume="1000",
            )
            collector.close()

            files = list(Path(tmpdir).glob("*.csv"))
            assert len(files) == 1
            assert "BTC-5m-" in files[0].name

            with open(files[0]) as f:
                reader = csv.reader(f)
                headers = next(reader)
                assert headers == CSV_HEADERS
                row = next(reader)
                assert row[1] == "btc-updown-5m-123"
                assert row[3] == "BTC"
                assert row[13] == "0.50"  # midpoint
                assert row[14] == "0.10"  # spread
                assert row[17] == "71000"  # spot
                assert row[18] == "71002"  # kraken
                assert row[19] == "71005"  # rtds
                assert row[21] == "snapshot"

    def test_round_end_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = RoundCollector("eth", "15m", data_dir=tmpdir)
            collector.write_round_end(
                slug="eth-updown-15m-999", condition_id="0xdef",
                end_date="2026-01-01T00:15:00Z",
                up_token_id="up_eth", down_token_id="down_eth",
                up_bid=Decimal("0.95"), up_ask=Decimal("0.99"),
                spot_price=Decimal("3500"), kraken_price=Decimal("3500.50"),
                rtds_price=Decimal("3501"), volume="500", outcome="up",
            )
            collector.close()

            files = list(Path(tmpdir).glob("*.csv"))
            with open(files[0]) as f:
                reader = csv.reader(f)
                next(reader)
                row = next(reader)
                assert row[6] == "0.0"
                assert row[21] == "round_end"
                assert row[22] == "up"

    def test_no_data_columns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = RoundCollector("sol", "5m", data_dir=tmpdir)
            collector.write_snapshot(
                slug="sol-updown-5m-123", condition_id="0x",
                end_date="2026-01-01T00:05:00Z", seconds_remaining=60.0,
                up_token_id="up", down_token_id="down",
                up_bid=None, up_ask=None, down_bid=None, down_ask=None,
                last_trade_price=None, last_trade_side="",
                spot_price=None, kraken_price=None, rtds_price=None,
                volume="0",
            )
            collector.close()

            files = list(Path(tmpdir).glob("*.csv"))
            with open(files[0]) as f:
                reader = csv.reader(f)
                next(reader)
                row = next(reader)
                assert row[9] == ""   # up_bid
                assert row[13] == ""  # midpoint
                assert row[17] == ""  # spot
                assert row[18] == ""  # kraken
                assert row[19] == ""  # rtds
