from src.freshness import parse_timestamp, within_24h
from datetime import datetime, timezone, timedelta


def test_parse_iso_timestamp():
    assert parse_timestamp("2026-11-21T12:00:00Z") is not None


def test_recent_timestamp():
    now = datetime.now(timezone.utc)
    assert within_24h((now - timedelta(hours=2)).isoformat(), now)


def test_old_timestamp_is_rejected():
    now = datetime.now(timezone.utc)
    assert not within_24h((now - timedelta(days=2)).isoformat(), now)


def test_relative_timestamp_is_parsed():
    parsed = parse_timestamp("2 hours ago")
    assert parsed is not None
