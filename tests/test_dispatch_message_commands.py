"""Tests for dispatch_message_commands: the /message_commands loop that
delivers free-form phone/Operator messages via deliver_human_message."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.logging_jsonl import JsonlLogger
from server.registry import Registry, Conversation, ConversationMember


def _make_supervisor():
	supervisor = MagicMock()
	supervisor.record_success = MagicMock()
	supervisor.record_crash = AsyncMock()
	return supervisor


def _make_backend():
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value=("corr", "msg-1"))
	backend.set_conversation_last_activity = AsyncMock()
	backend.mark_question_cancelled = AsyncMock()
	registered = {}

	async def fake_start_listener(handler):
		registered["handler"] = handler

	backend.start_message_command_listener = fake_start_listener
	return backend, registered


def _conv(registry, conv_id="conv-1", members=()):
	conv = Conversation(id=conv_id, title="t", state="active")
	for sid, sender in members:
		conv.members_active[sid] = ConversationMember(
			cli_session_id=sid, sender=sender, cwd="c:/w", surface="windows", joined_at=0.0,
		)
	registry.conversations[conv_id] = conv
	return conv


def _events(log_path):
	return [json.loads(line) for line in log_path.read_text().splitlines() if line]


@pytest.mark.asyncio
async def test_valid_command_delivers_message(tmp_path):
	"""A well-formed command reaches deliver_human_message: the history grows
	and the working member's session gets a queued notice. Discriminates
	against a dispatcher that never actually calls deliver_human_message."""
	from server.gateway.dispatch import dispatch_message_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	backend, registered = _make_backend()
	session_registry = MagicMock()
	session_registry.queue_notice.return_value = True
	supervisor = _make_supervisor()

	await dispatch_message_commands(registry, backend, logger, supervisor, session_registry=session_registry)
	await registered["handler"]({
		"conversation_id": "conv-1", "text": "hello there", "issued_at": "2026-07-30T00:00:00+00:00",
	})

	assert len(conv.messages) == 1
	assert conv.messages[0]["text"] == "hello there"
	session_registry.queue_notice.assert_called_once_with("sess-1", "John (from phone): hello there")
	backend.send_text.assert_not_awaited()
	supervisor.record_success.assert_called_once()
	supervisor.record_crash.assert_not_awaited()
	assert any(e["event"] == "info" and "message_command_handled" in e["detail"] for e in _events(log_path))


@pytest.mark.asyncio
async def test_leading_and_trailing_whitespace_is_stripped_before_delivery(tmp_path):
	"""Coverage rider for the earlier dispatch task's whitespace handling: a
	command whose text carries leading and trailing whitespace must reach
	deliver_human_message with that whitespace stripped, not the raw padded
	text. Discriminates against a regression that drops the .strip() call at
	the delivery call site."""
	from server.gateway.dispatch import dispatch_message_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	backend, registered = _make_backend()
	session_registry = MagicMock()
	session_registry.queue_notice.return_value = True
	supervisor = _make_supervisor()

	await dispatch_message_commands(registry, backend, logger, supervisor, session_registry=session_registry)
	await registered["handler"]({
		"conversation_id": "conv-1", "text": "  stop the build  ", "issued_at": "2026-07-30T00:00:00+00:00",
	})

	assert len(conv.messages) == 1
	assert conv.messages[0]["text"] == "stop the build"
	backend.write_conversation_message.assert_awaited_once_with(
		"conv-1", "John", "human", "stop the build", format="markdown", suppress_push=True,
	)
	supervisor.record_success.assert_called_once()
	supervisor.record_crash.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_conversation_id_is_invalid(tmp_path):
	"""A command with no conversation_id never reaches deliver_human_message;
	it logs message_command_invalid and still counts as a handled success (so
	a malformed entry doesn't trip the supervisor or replay forever)."""
	from server.gateway.dispatch import dispatch_message_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	backend, registered = _make_backend()
	supervisor = _make_supervisor()

	await dispatch_message_commands(registry, backend, logger, supervisor)
	await registered["handler"]({"text": "hello", "issued_at": "2026-07-30T00:00:00+00:00"})

	backend.write_conversation_message.assert_not_awaited()
	backend.send_text.assert_not_awaited()
	supervisor.record_success.assert_called_once()
	assert any(e["event"] == "surface_error" and "message_command_invalid" in e["detail"] for e in _events(log_path))


@pytest.mark.asyncio
async def test_blank_text_is_invalid(tmp_path):
	"""Whitespace-only text is rejected the same way as missing text: no
	delivery attempt, logged as message_command_invalid, success recorded."""
	from server.gateway.dispatch import dispatch_message_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	_conv(registry, members=[("sess-1", "Agent")])
	backend, registered = _make_backend()
	supervisor = _make_supervisor()

	await dispatch_message_commands(registry, backend, logger, supervisor)
	await registered["handler"]({
		"conversation_id": "conv-1", "text": "   ", "issued_at": "2026-07-30T00:00:00+00:00",
	})

	backend.write_conversation_message.assert_not_awaited()
	backend.send_text.assert_not_awaited()
	supervisor.record_success.assert_called_once()
	assert any(e["event"] == "surface_error" and "message_command_invalid" in e["detail"] for e in _events(log_path))


@pytest.mark.asyncio
async def test_unknown_conversation_sends_phone_feedback(tmp_path):
	"""A conversation_id that doesn't resolve to an active conversation makes
	deliver_human_message return delivered=False, which the dispatcher must
	surface back to the phone with the exact not-delivered wording."""
	from server.gateway.dispatch import dispatch_message_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	backend, registered = _make_backend()
	supervisor = _make_supervisor()

	await dispatch_message_commands(registry, backend, logger, supervisor)
	await registered["handler"]({
		"conversation_id": "conv-missing", "text": "hello", "issued_at": "2026-07-30T00:00:00+00:00",
	})

	backend.send_text.assert_awaited_once_with("Message not delivered: that conversation is no longer active.")
	supervisor.record_success.assert_called_once()
	supervisor.record_crash.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_failure_is_loud(tmp_path, monkeypatch):
	"""A raising delivery path routes through _report_command_failure: audit
	line, phone notice, supervisor crash record, and re-raise (so the
	firebase-side wrapper skips the command delete and it replays on restart).
	Mirrors tests/test_dispatch_command_failures.py's pattern for the other
	three listener-driven dispatchers."""
	from server.gateway.dispatch import dispatch_message_commands
	import server.inbound as inbound_mod

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	monkeypatch.setattr(inbound_mod, "deliver_human_message", AsyncMock(side_effect=RuntimeError("message boom")))
	backend, registered = _make_backend()
	supervisor = _make_supervisor()

	await dispatch_message_commands(registry, backend, logger, supervisor)

	with pytest.raises(RuntimeError, match="message boom"):
		await registered["handler"]({
			"conversation_id": "conv-1", "text": "hello", "issued_at": "2026-07-30T00:00:00+00:00",
		})

	supervisor.record_crash.assert_awaited_once()
	supervisor.record_success.assert_not_called()
	backend.send_text.assert_awaited_once()
	assert "Message delivery failed" in backend.send_text.await_args.args[0]
	assert any(e["event"] == "surface_error" and "message_command_failed" in e["detail"] for e in _events(log_path))
