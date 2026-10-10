"""QuotaStore: the account's plan usage as the sessions' mods last reported it."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from server.quota_store import QuotaStore

FIVE = {"kind": "five_hour", "percent_used": 10, "resets_at": "2026-10-10T23:20:00+00:00"}
SEVEN = {"kind": "seven_day", "percent_used": 73, "resets_at": "2026-10-13T19:00:00+00:00"}


def _ts(iso: str) -> float:
	return datetime.fromisoformat(iso).timestamp()


def test_an_empty_store_has_no_snapshot():
	assert QuotaStore().snapshot() is None


def test_apply_records_each_window_and_the_receive_time():
	store = QuotaStore()
	assert store.apply([FIVE, SEVEN], "2026-10-10T19:19:49+00:00") is True
	assert store.snapshot() == {
		"five_hour": {"percent_used": 10, "resets_at": "2026-10-10T23:20:00+00:00"},
		"seven_day": {"percent_used": 73, "resets_at": "2026-10-13T19:00:00+00:00"},
		"received_at": "2026-10-10T19:19:49+00:00",
	}


def test_apply_replaces_only_the_windows_it_carries():
	store = QuotaStore()
	store.apply([FIVE, SEVEN], "t1")
	store.apply([{**FIVE, "percent_used": 12}], "t2")
	snap = store.snapshot()
	assert (snap["five_hour"]["percent_used"], snap["seven_day"]["percent_used"], snap["received_at"]) == (12, 73, "t2")


def test_an_identical_report_is_not_a_change():
	store = QuotaStore()
	store.apply([FIVE], "t1")
	assert store.apply([FIVE], "t2") is False
	assert store.snapshot()["received_at"] == "t1"


def test_expire_zeroes_a_window_whose_reset_has_passed():
	store = QuotaStore()
	store.apply([FIVE, SEVEN], "t1")
	assert store.expire(_ts("2026-10-11T00:00:00+00:00")) is True
	snap = store.snapshot()
	assert snap["five_hour"] == {"percent_used": 0, "resets_at": None}
	assert snap["seven_day"]["percent_used"] == 73
	assert store.expire(_ts("2026-10-11T00:00:00+00:00")) is False


def test_expire_before_any_reset_changes_nothing():
	store = QuotaStore()
	store.apply([FIVE], "t1")
	assert store.expire(_ts("2026-10-10T20:00:00+00:00")) is False


def test_snapshot_is_a_copy():
	store = QuotaStore()
	store.apply([FIVE], "t1")
	store.snapshot()["five_hour"]["percent_used"] = 99
	assert store.snapshot()["five_hour"]["percent_used"] == 10


def test_load_restores_a_published_reading_and_skips_junk():
	store = QuotaStore()
	store.load({
		"five_hour": {"percent_used": 10, "resets_at": "2026-10-10T23:20:00+00:00"},
		"seven_day": "junk",
		"spend_limit": {"percent_used": 5},
		"received_at": "t0",
	})
	assert store.snapshot() == {
		"five_hour": {"percent_used": 10, "resets_at": "2026-10-10T23:20:00+00:00"},
		"received_at": "t0",
	}


def test_load_of_a_window_rtdb_stored_without_its_null_reset():
	# RTDB drops null children, so a zeroed window comes back without resets_at.
	store = QuotaStore()
	store.load({"five_hour": {"percent_used": 0}, "received_at": "t0"})
	assert store.snapshot()["five_hour"] == {"percent_used": 0, "resets_at": None}


def test_load_of_nothing_leaves_the_store_empty():
	store = QuotaStore()
	store.load(None)
	assert store.snapshot() is None


@pytest.mark.asyncio
async def test_firebase_write_quota_sets_and_none_deletes(monkeypatch):
	from server import firebase as fb_module
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	await be.write_quota({"received_at": "t"})
	mock_db.reference.assert_called_with("quota")
	mock_db.reference.return_value.set.assert_called_once_with({"received_at": "t"})
	await be.write_quota(None)
	mock_db.reference.return_value.delete.assert_called_once()


@pytest.mark.asyncio
async def test_firebase_read_quota_returns_a_dict_or_none(monkeypatch):
	from server import firebase as fb_module
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	mock_db.reference.return_value.get.return_value = {"received_at": "t"}
	assert await be.read_quota() == {"received_at": "t"}
	mock_db.reference.return_value.get.return_value = "junk"
	assert await be.read_quota() is None
