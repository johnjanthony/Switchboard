"""Tests for the phone-initiated away-mode toggle dispatcher."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.gateway.dispatch import dispatch_away_mode_commands
from server.logging_jsonl import JsonlLogger
from server.registry import Registry


def _make_supervisor():
	supervisor = MagicMock()
	supervisor.record_success = MagicMock()
	supervisor.record_crash = AsyncMock()
	return supervisor


def _make_backend(commands):
	"""Build a mock backend whose poll_away_mode_commands yields `commands` then
	raises CancelledError so the dispatcher exits cleanly."""
	backend = MagicMock()
	backend.set_global_away_mode = AsyncMock()
	backend.send_resolution_confirmation = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	backend.set_conversation_last_activity = AsyncMock()

	async def _poll():
		for cmd in commands:
			yield cmd
		raise asyncio.CancelledError()

	backend.poll_away_mode_commands = _poll
	return backend


@pytest.mark.asyncio
async def test_enter_global_flips_flag(tmp_path):
	"""enter_global command sets registry.global_away_mode True and persists."""
	registry = Registry()
	registry.global_away_mode = False

	backend = _make_backend([
		{"type": "enter_global", "issued_at": "2026-05-26T00:00:00Z"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is True
	backend.set_global_away_mode.assert_awaited_with(True)


@pytest.mark.asyncio
async def test_exit_global_flips_flag_no_bulk_respond_when_no_default_text(tmp_path):
	"""exit_global without default_text flips the flag and does not attempt bulk-respond."""
	registry = Registry()
	registry.global_away_mode = True

	backend = _make_backend([
		{"type": "exit_global", "issued_at": "2026-05-26T00:00:00Z"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is False
	backend.set_global_away_mode.assert_awaited_with(False)
	# No pending requests exist and no default_text — write_conversation_message should not be called.
	backend.write_conversation_message.assert_not_called()


@pytest.mark.asyncio
async def test_exit_global_triggers_bulk_respond_when_default_text_present(tmp_path):
	"""exit_global with default_text invokes _apply_bulk_respond_decision which
	resolves pending ask_human requests with the provided text."""
	registry = Registry()
	registry.global_away_mode = True

	# Plant a pending request so bulk_respond has something to resolve.
	future = registry.add("conv-aaa", "Claude", "req-001", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": "2026-05-26T00:00:00Z", "default_text": "Back soon"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is False
	# The pending future should have been resolved with the default text.
	assert future.done()
	assert future.result() == "Back soon"


@pytest.mark.asyncio
async def test_unknown_command_type_logs_error_and_continues(tmp_path):
	"""An unknown command type surfaces an error but does not abort the loop."""
	registry = Registry()
	registry.global_away_mode = False

	log_path = tmp_path / "log.jsonl"
	backend = _make_backend([
		{"type": "bogus_command", "issued_at": "2026-05-26T00:00:00Z"},
		# A valid command after the unknown one — the dispatcher should continue.
		{"type": "enter_global", "issued_at": "2026-05-26T00:00:01Z"},
	])
	logger = JsonlLogger(str(log_path))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	# The enter_global after the unknown command should still have been processed.
	assert registry.global_away_mode is True

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert any("away_mode_command_unknown_type" in e["detail"] for e in errors)


@pytest.mark.asyncio
async def test_exit_global_decision_cancel_does_not_flip(tmp_path):
	"""M07: the phone's decision field is authoritative. 'cancel' means leave
	away mode ON and leave pendings alone."""
	registry = Registry()
	registry.global_away_mode = True
	future = registry.add("conv-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": "2026-06-11T00:00:00Z", "decision": "cancel"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is True, "cancel must not commit the flip"
	assert not future.done(), "cancel must leave pendings alone"


@pytest.mark.asyncio
async def test_exit_global_send_default_blank_text_is_rejected(tmp_path):
	"""M07: 'Send to all' with blank text used to silently degrade to skip.
	It must surface a validation error and not flip the flag."""
	registry = Registry()
	registry.global_away_mode = True
	future = registry.add("conv-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": "2026-06-11T00:00:00Z", "decision": "send_default", "default_text": ""},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is True, "blank send_default must not commit the flip"
	assert not future.done()


@pytest.mark.asyncio
async def test_exit_global_decision_skip_flips_but_leaves_pendings(tmp_path):
	registry = Registry()
	registry.global_away_mode = True
	future = registry.add("conv-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": "2026-06-11T00:00:00Z", "decision": "skip"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is False
	assert not future.done(), "skip leaves pendings in place"
