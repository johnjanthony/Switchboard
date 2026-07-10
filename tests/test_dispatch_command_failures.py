"""REV-105/DT-6: a crashing command handler must be loud - JSONL audit line,
phone notice, supervisor crash record - and must re-raise so the firebase-side
wrapper skips the command delete (at-least-once replay on restart)."""

from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.session_registry import SessionRegistry


def _make_supervisor():
	supervisor = MagicMock()
	supervisor.record_success = MagicMock()
	supervisor.record_crash = AsyncMock()
	return supervisor


def _make_backend(listener_attr):
	backend = MagicMock()
	backend.send_text = AsyncMock()
	registered = {}

	async def fake_start_listener(handler):
		registered["handler"] = handler

	setattr(backend, listener_attr, fake_start_listener)
	return backend, registered


def _events(log_path):
	return [json.loads(line) for line in log_path.read_text().splitlines() if line]


@pytest.mark.asyncio
async def test_combine_handler_failure_is_loud(tmp_path, monkeypatch):
	from server.gateway.dispatch import dispatch_combine_commands
	import server.conversation_ops as conv_ops

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	monkeypatch.setattr(conv_ops, "_perform_combine", AsyncMock(side_effect=RuntimeError("combine boom")))
	backend, registered = _make_backend("start_combine_command_listener")
	supervisor = _make_supervisor()

	await dispatch_combine_commands(Registry(), backend, logger, supervisor)

	with pytest.raises(RuntimeError, match="combine boom"):
		await registered["handler"]({
			"source_conversation_id": "conv-src",
			"target_conversation_id": "conv-tgt",
			"issued_at": "2026-07-09T00:00:00+00:00",
		})

	supervisor.record_crash.assert_awaited_once()
	supervisor.record_success.assert_not_called()
	backend.send_text.assert_awaited_once()
	assert "Combine failed" in backend.send_text.await_args.args[0]
	assert any(e["event"] == "surface_error" and "combine_command_failed" in e["detail"] for e in _events(log_path))


@pytest.mark.asyncio
async def test_force_end_handler_failure_is_loud(tmp_path, monkeypatch):
	from server.gateway import dispatch as dispatch_mod

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	monkeypatch.setattr(dispatch_mod, "handle_force_end", AsyncMock(side_effect=RuntimeError("force-end boom")))
	backend, registered = _make_backend("start_force_end_command_listener")
	supervisor = _make_supervisor()

	await dispatch_mod.dispatch_force_end_commands(Registry(), backend, logger, supervisor)

	with pytest.raises(RuntimeError, match="force-end boom"):
		await registered["handler"]({"conversation_id": "conv-xyz", "issued_at": "2026-07-09T00:00:00+00:00"})

	supervisor.record_crash.assert_awaited_once()
	supervisor.record_success.assert_not_called()
	backend.send_text.assert_awaited_once()
	assert "conv-xyz" in backend.send_text.await_args.args[0]
	assert any(e["event"] == "surface_error" and "force_end_command_failed" in e["detail"] for e in _events(log_path))


@pytest.mark.asyncio
async def test_convene_handler_failure_is_loud(tmp_path, monkeypatch):
	from server.gateway.dispatch import dispatch_convene_commands
	import server.conversation_ops as conv_ops

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	monkeypatch.setattr(conv_ops, "_perform_convene", AsyncMock(side_effect=RuntimeError("convene boom")))
	backend, registered = _make_backend("start_convene_command_listener")
	supervisor = _make_supervisor()

	await dispatch_convene_commands(Registry(), SessionRegistry(), backend, logger, supervisor)

	with pytest.raises(RuntimeError, match="convene boom"):
		await registered["handler"]({"session_ids": ["s-1"], "target": "new", "issued_at": "2026-07-09T00:00:00+00:00"})

	supervisor.record_crash.assert_awaited_once()
	supervisor.record_success.assert_not_called()
	backend.send_text.assert_awaited_once()
	assert "Convene failed" in backend.send_text.await_args.args[0]
	assert any(e["event"] == "surface_error" and "convene_command_failed" in e["detail"] for e in _events(log_path))


@pytest.mark.asyncio
async def test_spawn_handler_failure_is_loud(tmp_path):
	from server.gateway.dispatch import dispatch_spawn_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	spawn_handler = MagicMock()
	spawn_handler.handle_fresh = AsyncMock(side_effect=RuntimeError("spawn boom"))
	backend, registered = _make_backend("start_spawn_command_listener")
	supervisor = _make_supervisor()

	await dispatch_spawn_commands(spawn_handler, backend, logger, supervisor)

	with pytest.raises(RuntimeError, match="spawn boom"):
		await registered["handler"]({"type": "fresh", "project": "X", "issued_at": "2026-07-09T00:00:00+00:00"})

	supervisor.record_crash.assert_awaited_once()
	supervisor.record_success.assert_not_called()
	backend.send_text.assert_awaited_once()
	assert "re-issue from the phone" in backend.send_text.await_args.args[0]
	assert any(e["event"] == "surface_error" and "spawn_command_failed" in e["detail"] for e in _events(log_path))
