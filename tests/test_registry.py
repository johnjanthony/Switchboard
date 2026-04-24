"""Tests for the pending-request registry."""

import asyncio
import json
from datetime import datetime

import pytest

from server.registry import PendingRequest, Registry


@pytest.mark.asyncio
async def test_add_returns_future_and_stores_record():
	registry = Registry()
	future = registry.add("abc123", "chan-1", correlation=42)
	assert isinstance(future, asyncio.Future)
	record = registry.get("abc123")
	assert isinstance(record, PendingRequest)
	assert record.request_id == "abc123"
	assert record.channel_id == "chan-1"
	assert record.correlation == 42
	assert record.future is future


@pytest.mark.asyncio
async def test_resolve_by_correlation_sets_future_result_and_returns_request_id():
	registry = Registry()
	future = registry.add("abc123", "chan-1", correlation=42)
	record = registry.resolve_by_correlation(42, "yes")
	assert record.request_id == "abc123"
	assert future.done()
	assert future.result() == "yes"


@pytest.mark.asyncio
async def test_resolve_by_correlation_returns_none_for_unknown_correlation():
	registry = Registry()
	registry.add("abc123", "chan-1", correlation=42)
	assert registry.resolve_by_correlation(999, "late") is None


@pytest.mark.asyncio
async def test_resolve_removes_entry_from_pending():
	registry = Registry()
	registry.add("abc123", "chan-1", correlation=42)
	registry.resolve_by_correlation(42, "yes")
	assert registry.get("abc123") is None


@pytest.mark.asyncio
async def test_remove_drops_both_indexes():
	registry = Registry()
	registry.add("abc123", "chan-1", correlation=42)
	registry.remove("abc123")
	assert registry.get("abc123") is None
	assert registry.resolve_by_correlation(42, "late") is None


@pytest.mark.asyncio
async def test_multiple_pending_are_independent():
	registry = Registry()
	f1 = registry.add("a", "chan-a", correlation=1)
	f2 = registry.add("b", "chan-b", correlation=2)
	registry.resolve_by_correlation(2, "answer-b")
	assert f2.done() and f2.result() == "answer-b"
	assert not f1.done()
	registry.resolve_by_correlation(1, "answer-a")
	assert f1.done() and f1.result() == "answer-a"


def test_away_mode_defaults_false_when_no_path():
	registry = Registry()
	assert registry.is_away_mode_active() is False


def test_away_mode_defaults_false_when_file_missing(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	assert registry.is_away_mode_active() is False


def test_away_mode_loads_true_from_file(tmp_path):
	path = tmp_path / "away-mode.json"
	path.write_text(
		'{"active": true, "entered_at": "2026-04-23T14:30:00+00:00"}',
		encoding="utf-8",
	)
	registry = Registry(away_mode_path=path)
	assert registry.is_away_mode_active() is True


def test_away_mode_set_true_persists(tmp_path):
	path = tmp_path / "away-mode.json"
	registry = Registry(away_mode_path=path)
	registry.set_away_mode(True)
	data = json.loads(path.read_text(encoding="utf-8"))
	assert data["active"] is True
	assert isinstance(data["entered_at"], str)
	# Valid ISO format
	datetime.fromisoformat(data["entered_at"])


def test_away_mode_set_false_persists(tmp_path):
	path = tmp_path / "away-mode.json"
	registry = Registry(away_mode_path=path)
	registry.set_away_mode(True)
	registry.set_away_mode(False)
	data = json.loads(path.read_text(encoding="utf-8"))
	assert data["active"] is False
	assert data["entered_at"] is None


def test_away_mode_round_trip_across_registry_instances(tmp_path):
	path = tmp_path / "away-mode.json"
	r1 = Registry(away_mode_path=path)
	r1.set_away_mode(True)
	r2 = Registry(away_mode_path=path)
	assert r2.is_away_mode_active() is True


def test_away_mode_corrupt_file_defaults_false(tmp_path):
	path = tmp_path / "away-mode.json"
	path.write_text("not json at all {{{", encoding="utf-8")
	registry = Registry(away_mode_path=path)
	assert registry.is_away_mode_active() is False


def test_away_mode_set_is_idempotent(tmp_path):
	path = tmp_path / "away-mode.json"
	registry = Registry(away_mode_path=path)
	registry.set_away_mode(True)
	registry.set_away_mode(True)
	assert registry.is_away_mode_active() is True
	registry.set_away_mode(False)
	registry.set_away_mode(False)
	assert registry.is_away_mode_active() is False


def test_away_mode_no_path_set_does_not_crash():
	registry = Registry()  # no path
	registry.set_away_mode(True)
	assert registry.is_away_mode_active() is True
	registry.set_away_mode(False)
	assert registry.is_away_mode_active() is False
