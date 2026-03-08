"""Tests for bots/kalshi_crypto/discovery.py."""

from __future__ import annotations

from bots.kalshi_crypto.discovery import _parse_time


class TestParseTime:
    def test_z_suffix(self):
        dt = _parse_time("2026-03-05T01:45:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 5

    def test_offset_suffix(self):
        dt = _parse_time("2026-03-05T01:45:00+00:00")
        assert dt is not None

    def test_none_input(self):
        assert _parse_time(None) is None

    def test_empty_string(self):
        assert _parse_time("") is None

    def test_invalid_string(self):
        assert _parse_time("not-a-date") is None
