"""The server heartbeat loop: presence/server_at, written at once and then
every interval."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


async def _pump():
	for _ in range(5):
		await asyncio.sleep(0)


def _supervisor(tmp_path):
	from server.logging_jsonl import JsonlLogger
	from tests.conftest import _make_loop_supervisor
	return _make_loop_supervisor(None, JsonlLogger(str(tmp_path / "log.jsonl")), "dispatch_presence")


@pytest.mark.asyncio
async def test_presence_is_written_at_once_and_records_success(tmp_path):
	from server.gateway.dispatch import dispatch_presence
	backend = MagicMock()
	backend.write_presence = AsyncMock()
	supervisor = _supervisor(tmp_path)
	task = asyncio.create_task(dispatch_presence(backend, supervisor, interval=10.0, now=lambda: "2026-10-10T12:00:00+00:00"))
	await _pump()
	backend.write_presence.assert_awaited_once_with("2026-10-10T12:00:00+00:00")
	assert supervisor.health().consecutive_failures == 0
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_a_failed_presence_write_is_a_supervised_crash(tmp_path):
	from server.gateway.dispatch import dispatch_presence
	backend = MagicMock()
	backend.write_presence = AsyncMock(side_effect=RuntimeError("firebase down"))
	supervisor = _supervisor(tmp_path)
	task = asyncio.create_task(dispatch_presence(backend, supervisor, interval=10.0))
	await _pump()
	assert supervisor.health().consecutive_failures == 1
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_firebase_write_presence_sets_server_at(monkeypatch):
	from server import firebase as fb_module
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	await be.write_presence("2026-10-10T12:00:00+00:00")
	mock_db.reference.assert_called_with("presence/server_at")
	mock_db.reference.return_value.set.assert_called_once_with("2026-10-10T12:00:00+00:00")
