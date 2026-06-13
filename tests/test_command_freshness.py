"""Freshness gate for phone-issued commands (P2-1, decided 2026-06-11):
commands older than the TTL are dropped with a notice instead of executing a
surprise combine/spawn hours later. Missing or malformed stamps fail OPEN
(treated as fresh): a parse quirk must not silently drop a command John just
sent; every current writer stamps issued_at."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.command_freshness import COMMAND_TTL_SECONDS, command_age_seconds


def _iso(delta_seconds: float) -> str:
	return (datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)).isoformat()


def test_fresh_command_age_is_small():
	age = command_age_seconds(_iso(5))
	assert age is not None and 0 <= age < 60


def test_stale_command_age_exceeds_ttl():
	age = command_age_seconds(_iso(COMMAND_TTL_SECONDS + 300))
	assert age is not None and age > COMMAND_TTL_SECONDS


def test_zulu_suffix_is_accepted():
	iso = _iso(5).replace("+00:00", "Z")
	age = command_age_seconds(iso)
	assert age is not None and 0 <= age < 60


def test_missing_stamp_fails_open():
	assert command_age_seconds(None) is None
	assert command_age_seconds("") is None


def test_garbage_stamp_fails_open():
	assert command_age_seconds("not-a-timestamp") is None


def test_naive_stamp_is_treated_as_utc():
	naive = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(tzinfo=None).isoformat()
	age = command_age_seconds(naive)
	assert age is not None and 0 <= age < 90
