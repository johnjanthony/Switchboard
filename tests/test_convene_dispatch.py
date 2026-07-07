"""Tests for dispatch_convene_commands routing (Task 5)."""

from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.logging_jsonl import JsonlLogger
from server.session_registry import SessionRegistry
from tests.conftest import make_registry_with_loopback


@pytest.mark.asyncio
async def test_dispatch_convene_command_invokes_perform_convene_and_logs(tmp_path):
	"""_handle inside dispatch_convene_commands calls _perform_convene with the
	session ids from the command dict, routes both live sessions into a freshly
	minted conversation, and logs convene_command_handled on success."""
	from server.gateway.dispatch import dispatch_convene_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("cs-alpha-0001", cwd="C:/Work/A")
	session_registry.set_sender("cs-alpha-0001", "Claude Win")
	session_registry.record_session_start("cs-bravo-0002", cwd="C:/Work/B")
	session_registry.set_sender("cs-bravo-0002", "Claude WSL")

	backend = MagicMock()
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_member = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	backend.set_conversation_last_activity = AsyncMock()
	backend.set_session_home = AsyncMock()

	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_convene_command_listener = fake_start_listener

	await dispatch_convene_commands(registry, session_registry, backend, logger, supervisor=None)

	assert registered_handler is not None, "handler should have been registered"

	await registered_handler({
		"session_ids": ["cs-alpha-0001", "cs-bravo-0002"],
		"target": "new",
		"title": None,
		"issued_at": "2026-07-06T00:00:00+00:00",
	})

	conv_ids = [cid for cid in registry.conversations if cid.startswith("conv-")]
	assert len(conv_ids) == 1
	conv = registry.conversations[conv_ids[0]]
	assert set(conv.members_active) == {"cs-alpha-0001", "cs-bravo-0002"}
	assert registry.session_to_conversation_id["cs-alpha-0001"] == conv_ids[0]
	assert registry.session_to_conversation_id["cs-bravo-0002"] == conv_ids[0]

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	infos = [e for e in events if e["event"] == "info"]
	assert any("convene_command_handled" in e["detail"] for e in infos)


@pytest.mark.asyncio
async def test_dispatch_convene_command_missing_or_empty_session_ids(tmp_path):
	"""_handle logs convene_command_missing_sessions and does not crash when
	session_ids is absent, not a list, or an empty list."""
	from server.gateway.dispatch import dispatch_convene_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	backend = MagicMock()
	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_convene_command_listener = fake_start_listener

	await dispatch_convene_commands(registry, session_registry, backend, logger, supervisor=None)
	assert registered_handler is not None

	# Missing key entirely.
	await registered_handler({"target": "new", "issued_at": "2026-07-06T00:00:00+00:00"})
	# Empty list.
	await registered_handler({"session_ids": [], "target": "new", "issued_at": "2026-07-06T00:00:00+00:00"})
	# Not a list at all.
	await registered_handler({"session_ids": "not-a-list", "target": "new", "issued_at": "2026-07-06T00:00:00+00:00"})

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert len(errors) == 3
	assert all("convene_command_missing_sessions" in e["detail"] for e in errors)
	assert registry.conversations == {}


@pytest.mark.asyncio
async def test_dispatch_convene_command_all_skipped_notifies_phone(tmp_path):
	"""When every requested session is skipped, a phone-visible notice naming
	each skip reason goes out via backend.send_text (the command entry itself
	is deleted by the listener lifecycle, so this notice is the only record
	of a no-op convene)."""
	from server.gateway.dispatch import dispatch_convene_commands
	from server.registry import ConversationMember
	from tests.conftest import make_active_conversation

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	# s-dead: a terminal session, no spawn_handler wired -> skip reason
	# "resume unavailable" (the fork-arm can't resume without one).
	session_registry.record_session_start("s-dead", cwd="C:/Work/D")
	session_registry.record_session_end("s-dead", reason="logout", ended_at="2026-07-06T00:00:00+00:00")

	# s-multi: a live session bound to a conversation with another alive
	# member -> skip reason "in a multi-party conversation".
	session_registry.record_session_start("s-multi", cwd="C:/Work/M")
	session_registry.set_sender("s-multi", "Multi")
	conv = make_active_conversation(conversation_id="conv-multi", member_session_id="s-multi", sender="Multi", cwd="C:/Work/M")
	other = ConversationMember(cli_session_id="s-other", sender="Other", cwd="C:/Work/O", surface="windows", joined_at=0.0)
	conv.members_active["s-other"] = other
	registry.conversations["conv-multi"] = conv
	registry.bind_session("s-multi", "conv-multi")
	registry.bind_session("s-other", "conv-multi")

	backend = MagicMock()
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	backend.set_conversation_last_activity = AsyncMock()
	backend.send_text = AsyncMock()

	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_convene_command_listener = fake_start_listener

	await dispatch_convene_commands(registry, session_registry, backend, logger, supervisor=None)
	assert registered_handler is not None

	await registered_handler({
		"session_ids": ["s-dead", "s-multi"],
		"target": "new",
		"title": None,
		"issued_at": "2026-07-06T00:00:00+00:00",
	})

	backend.send_text.assert_awaited_once()
	notice = backend.send_text.await_args.args[0]
	assert "resume unavailable" in notice
	assert "in a multi-party conversation" in notice

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	infos = [e for e in events if e["event"] == "info"]
	assert any("convene_command_handled" in e["detail"] for e in infos)


@pytest.mark.asyncio
async def test_convene_all_resuming_no_phone_noop_notice(tmp_path):
	"""When a convene command's only session is ended but a spawn_handler is
	wired, the fork-arm resumes it into "resuming" rather than skipping it -
	the "did nothing" phone notice must NOT fire, and the outcome is still
	logged."""
	from server.gateway.dispatch import dispatch_convene_commands
	from tests.test_convene import FakeSpawnHandler

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-resumable", cwd="C:/Work/R")
	session_registry.set_sender("s-resumable", "Resumable")
	session_registry.record_session_end("s-resumable", reason="logout", ended_at="2026-07-06T00:00:00+00:00")

	backend = MagicMock()
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_member = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	backend.set_conversation_last_activity = AsyncMock()
	backend.set_session_home = AsyncMock()
	backend.send_text = AsyncMock()

	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_convene_command_listener = fake_start_listener
	spawn_handler = FakeSpawnHandler()

	await dispatch_convene_commands(registry, session_registry, backend, logger, supervisor=None, spawn_handler=spawn_handler)
	assert registered_handler is not None

	await registered_handler({
		"session_ids": ["s-resumable"],
		"target": "new",
		"title": None,
		"issued_at": "2026-07-06T00:00:00+00:00",
	})

	backend.send_text.assert_not_awaited()
	assert len(spawn_handler.calls) == 1

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	infos = [e for e in events if e["event"] == "info"]
	assert any("convene_command_handled" in e["detail"] for e in infos)
