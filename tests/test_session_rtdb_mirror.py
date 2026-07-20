"""Verify the SessionRegistry mirror wiring fans session records out to RTDB
via FirebaseBackend.write_session_record / delete_session_record."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from server.gateway.bg_tasks import _spawn_bg
from server.session_registry import SessionRegistry


def _make_fake_backend():
	"""Fake backend recording write_session_record/delete_session_record calls,
	following the fake-backend style in tests/test_firebase_writes_for_handlers.py."""
	backend = MagicMock()
	backend.write_session_record = AsyncMock()
	backend.delete_session_record = AsyncMock()
	return backend


def _install_mirror(session_registry: SessionRegistry, backend) -> None:
	"""Copy of the _session_mirror closure main.py registers after
	registry.sessions = session_registry."""
	def _session_mirror(sid: str, payload: dict | None) -> None:
		if payload is None:
			_spawn_bg(backend.delete_session_record(sid), label=f"fb_session_delete:{sid}")
		else:
			_spawn_bg(backend.write_session_record(sid, payload), label=f"fb_session_write:{sid}")
	session_registry.set_mirror(_session_mirror)


async def _drain_bg():
	"""Yield control so _spawn_bg tasks get a chance to run."""
	for _ in range(5):
		await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_record_session_start_fires_one_write_with_full_payload():
	session_registry = SessionRegistry(now=lambda: "2026-01-01T00:00:00+00:00")
	backend = _make_fake_backend()
	_install_mirror(session_registry, backend)

	rec = session_registry.record_session_start("s-1", cwd="C:/Work/X", cli="claude")
	await _drain_bg()

	backend.write_session_record.assert_awaited_once_with("s-1", rec.to_payload())
	backend.delete_session_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_identical_refire_writes_nothing():
	session_registry = SessionRegistry(now=lambda: "2026-01-01T00:00:00+00:00")
	backend = _make_fake_backend()
	_install_mirror(session_registry, backend)

	session_registry.record_session_start("s-1", cwd="C:/Work/X", cli="claude")
	await _drain_bg()
	backend.write_session_record.assert_awaited_once()

	# touch_mcp with the same cwd/sender produces an identical payload (same
	# last_event_at because `now` is frozen), so the registry's internal diff
	# must suppress the second mirror fire.
	session_registry.touch_mcp("s-1", cwd="C:/Work/X")
	await _drain_bg()

	backend.write_session_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_pruning_terminal_record_fires_delete():
	session_registry = SessionRegistry(now=lambda: "2026-01-01T00:00:00+00:00")
	backend = _make_fake_backend()
	_install_mirror(session_registry, backend)

	session_registry.record_session_start("s-1", cwd="C:/Work/X", cli="claude")
	session_registry.record_session_end("s-1", reason="logout", ended_at="2026-01-01T00:00:00+00:00")
	await _drain_bg()

	pruned = session_registry.sweep(
		now_ts=1767225600.0 + 10.0,  # 10s after the fixed ended_at, past a 1s retention
		lost_after_seconds=60.0,
		retention_seconds=1.0,
		rings_fresh=True,
		ring_ids=set(),
	)
	await _drain_bg()

	assert pruned == ["s-1"]
	backend.delete_session_record.assert_awaited_once_with("s-1")


@pytest.mark.asyncio
async def test_delete_session_record_deletes_both_session_and_ack_paths(monkeypatch):
	"""FirebaseBackend.delete_session_record (the real implementation, not the
	recording fake used above) must delete both sessions/<id> and
	session_acks/<id>, so a pruned session never leaves an orphan ack entry.

	Constructed via __new__ (skips __init__'s firebase_admin call), following
	the pattern in tests/test_firebase_command_listeners.py's _make_backend."""
	from server import firebase as fb_module

	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)

	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)

	await be.delete_session_record("s-1")

	referenced_paths = [c.args[0] for c in mock_db.reference.call_args_list]
	assert "sessions/s-1" in referenced_paths
	assert "session_acks/s-1" in referenced_paths
	assert mock_db.reference.return_value.delete.call_count == 2
