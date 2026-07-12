"""Tests for the phone-initiated away-mode toggle dispatcher."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def _now_iso() -> str:
	"""Return the current UTC time as an ISO-8601 string (fresh, within TTL)."""
	return datetime.now(timezone.utc).isoformat()

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
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	backend.set_conversation_last_activity = AsyncMock()
	backend.send_text = AsyncMock()

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
		{"type": "enter_global", "issued_at": _now_iso()},
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
		{"type": "exit_global", "issued_at": _now_iso()},
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
	future = registry.add("conv-aaa", "s-aaa", "Claude", "req-001", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "default_text": "Back soon"},
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
		{"type": "bogus_command", "issued_at": _now_iso()},
		# A valid command after the unknown one — the dispatcher should continue.
		{"type": "enter_global", "issued_at": _now_iso()},
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
	future = registry.add("conv-1", "s-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "decision": "cancel"},
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
	future = registry.add("conv-1", "s-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "decision": "send_default", "default_text": ""},
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
	future = registry.add("conv-1", "s-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "decision": "skip"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is False
	assert not future.done(), "skip leaves pendings in place"


@pytest.mark.asyncio
async def test_stale_away_command_is_dropped_with_notice(tmp_path):
	"""P2-1 belt-and-braces for P1-5 (M06): a stale away toggle that survived
	the startup clear (crash-before-delete replay) must not flip the flag; it
	is dropped with a phone-visible notice."""
	from datetime import datetime, timedelta, timezone
	from server.command_freshness import COMMAND_TTL_SECONDS

	registry = Registry()
	registry.global_away_mode = False

	stale_iso = (datetime.now(timezone.utc) - timedelta(seconds=COMMAND_TTL_SECONDS + 600)).isoformat()
	backend = _make_backend([
		{"type": "enter_global", "issued_at": stale_iso},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is False, "a stale enter_global must not re-enable away mode"
	backend.send_text.assert_awaited_once()
	notice = backend.send_text.await_args.args[0]
	assert "stale" in notice.lower() and stale_iso in notice


@pytest.mark.asyncio
async def test_exit_global_flips_flag_before_drain_awaits(tmp_path):
	# REV-002: the drain suspends on Firebase writes; the flag must already be
	# False by then, so a concurrently-arriving ask_human takes the at-desk
	# redirect instead of registering a pending the drain's snapshot missed.
	registry = Registry()
	registry.global_away_mode = True
	registry.add("conv-1", "s-1", "Claude", request_id="req-1", msg_id="msg-1")

	flag_at_drain_write = []
	backend = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "decision": "send_default", "default_text": "Back"},
	])

	async def _spy_write(*args, **kwargs):
		flag_at_drain_write.append(registry.global_away_mode)
		return "key-1"

	backend.write_conversation_message = AsyncMock(side_effect=_spy_write)
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert flag_at_drain_write, "the drain should have written the bulk reply"
	assert all(v is False for v in flag_at_drain_write), \
		"global_away_mode must be False before the drain's first await (REV-002)"
	assert registry.global_away_mode is False


@pytest.mark.asyncio
async def test_exit_global_cancel_restores_prior_flag_value(tmp_path):
	# The non-commit restore must restore the PRE-COMMAND value, not assume
	# True: an exit_global with decision=cancel arriving when away mode is
	# already off must leave it off.
	registry = Registry()
	registry.global_away_mode = False
	registry.add("conv-1", "s-1", "Claude", request_id="req-1", msg_id="msg-1")

	backend = _make_backend([
		{"type": "exit_global", "issued_at": _now_iso(), "decision": "cancel"},
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_supervisor()

	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(registry, backend, logger, supervisor)

	assert registry.global_away_mode is False, "restore must not re-enable away mode on a double-exit"
