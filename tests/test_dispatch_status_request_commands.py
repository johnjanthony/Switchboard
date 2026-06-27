"""Tests for the phone-initiated Claude-status request dispatcher."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.command_freshness import COMMAND_TTL_SECONDS
from server.gateway.dispatch import dispatch_status_request_commands
from server.logging_jsonl import JsonlLogger


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _make_supervisor():
	supervisor = MagicMock()
	supervisor.record_success = MagicMock()
	supervisor.record_crash = AsyncMock()
	return supervisor


def _make_backend(commands):
	"""Backend whose poll_status_request_commands yields `commands` then raises
	CancelledError so the dispatcher exits cleanly (mirrors the away-mode test)."""
	backend = MagicMock()

	async def _poll():
		for cmd in commands:
			yield cmd
		raise asyncio.CancelledError()

	backend.poll_status_request_commands = _poll
	return backend


def _make_service():
	service = MagicMock()
	service.check = AsyncMock()
	service.stop = AsyncMock()
	return service


@pytest.mark.asyncio
async def test_check_command_calls_service_check(tmp_path):
	service = _make_service()
	backend = _make_backend([{"type": "check", "issued_at": _now_iso()}])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	with pytest.raises(asyncio.CancelledError):
		await dispatch_status_request_commands(service, backend, logger, _make_supervisor())
	service.check.assert_awaited_once()
	service.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_command_calls_service_stop(tmp_path):
	service = _make_service()
	backend = _make_backend([{"type": "stop", "issued_at": _now_iso()}])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	with pytest.raises(asyncio.CancelledError):
		await dispatch_status_request_commands(service, backend, logger, _make_supervisor())
	service.stop.assert_awaited_once()
	service.check.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_command_is_dropped(tmp_path):
	service = _make_service()
	stale = (datetime.now(timezone.utc) - timedelta(seconds=COMMAND_TTL_SECONDS + 600)).isoformat()
	backend = _make_backend([{"type": "check", "issued_at": stale}])
	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	with pytest.raises(asyncio.CancelledError):
		await dispatch_status_request_commands(service, backend, logger, _make_supervisor())
	service.check.assert_not_awaited()
	events = [json.loads(l) for l in log_path.read_text().splitlines() if l]
	assert any("status_request_command_stale_dropped" in e.get("detail", "") for e in events)


@pytest.mark.asyncio
async def test_unknown_type_logs_and_continues(tmp_path):
	service = _make_service()
	backend = _make_backend([
		{"type": "bogus", "issued_at": _now_iso()},
		{"type": "check", "issued_at": _now_iso()},
	])
	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	with pytest.raises(asyncio.CancelledError):
		await dispatch_status_request_commands(service, backend, logger, _make_supervisor())
	# The check after the unknown command still ran.
	service.check.assert_awaited_once()
	events = [json.loads(l) for l in log_path.read_text().splitlines() if l]
	assert any("status_request_command_unknown_type" in e.get("detail", "") for e in events)
