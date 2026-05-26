"""Tests for dispatch_combine_commands and dispatch_force_end_commands routing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.logging_jsonl import JsonlLogger


@pytest.fixture
def logger(tmp_path):
	return JsonlLogger(str(tmp_path / "log.jsonl"))


# ---------------------------------------------------------------------------
# dispatch_combine_commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_combine_command_invokes_perform_combine(logger):
	"""_handle inside dispatch_combine_commands calls _perform_combine with the
	conv ids extracted from the command dict.

	We use two real (empty) active conversations so _perform_combine passes its
	validation guards. The result confirms the correct ids were forwarded.
	"""
	from server.gateway.dispatch import dispatch_combine_commands
	from server.registry import Registry, Conversation, ConversationMember

	registry = Registry()
	for cid in ("conv-src", "conv-tgt"):
		conv = Conversation(id=cid, title=cid)
		registry.conversations[cid] = conv

	# Add a movable member to source so _perform_combine doesn't reject with
	# "source has no movable members".
	src_conv = registry.conversations["conv-src"]
	m = ConversationMember(
		cli_session_id="s-1",
		sender="Agent",
		cwd="C:/Work/X",
		surface="windows",
		joined_at=0.0,
	)
	src_conv.members_active["Agent"] = m
	registry.bind_session("s-1", "conv-src")

	backend = MagicMock()
	backend.remove_conversation_member = AsyncMock()
	backend.write_conversation_member = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	backend.set_conversation_state = AsyncMock()
	backend.set_conversation_last_activity = AsyncMock()
	backend.set_open_conversation_id = AsyncMock()
	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_combine_command_listener = fake_start_listener

	await dispatch_combine_commands(registry, backend, logger, supervisor=None)

	assert registered_handler is not None, "handler should have been registered"

	await registered_handler({
		"source_conversation_id": "conv-src",
		"target_conversation_id": "conv-tgt",
		"issued_at": "2026-05-25T00:00:00+00:00",
	})

	# _perform_combine should have ended source and moved the member to target
	assert registry.conversations["conv-src"].state == "ended"
	assert "Agent" in registry.conversations["conv-tgt"].members_active


@pytest.mark.asyncio
async def test_dispatch_combine_command_logs_missing_ids(logger, tmp_path):
	"""_handle logs an error when ids are absent from the command dict."""
	from server.gateway.dispatch import dispatch_combine_commands
	from server.registry import Registry
	import json

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	backend = MagicMock()

	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_combine_command_listener = fake_start_listener

	await dispatch_combine_commands(registry, backend, logger, supervisor=None)
	assert registered_handler is not None

	await registered_handler({"issued_at": "2026-05-25T00:00:00+00:00"})

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert any("combine_command_missing_ids" in e["detail"] for e in errors)


# ---------------------------------------------------------------------------
# dispatch_force_end_commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_force_end_command_invokes_handle_force_end(logger):
	"""_handle inside dispatch_force_end_commands calls handle_force_end with the
	conversation_id extracted from the command dict.

	We use a real active conversation with one member so handle_force_end can
	mutate state and we can confirm it ran.
	"""
	from server.gateway.dispatch import dispatch_force_end_commands
	from server.registry import Registry, Conversation, ConversationMember

	registry = Registry()
	conv = Conversation(id="conv-xyz", title="test")
	m = ConversationMember(cli_session_id="s-1", sender="Agent", cwd="C:/X", surface="windows", joined_at=0.0)
	conv.members_active["Agent"] = m
	registry.conversations["conv-xyz"] = conv
	registry.bind_session("s-1", "conv-xyz")

	backend = MagicMock()
	backend.remove_conversation_member = AsyncMock()
	backend.set_conversation_state = AsyncMock()
	backend.set_open_conversation_id = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key-1")
	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_force_end_command_listener = fake_start_listener

	await dispatch_force_end_commands(registry, backend, logger, supervisor=None)

	assert registered_handler is not None

	await registered_handler({"conversation_id": "conv-xyz", "issued_at": "2026-05-25T00:00:00+00:00"})

	# handle_force_end should have ended the conversation
	assert conv.state == "ended"
	assert conv.ended_at is not None


@pytest.mark.asyncio
async def test_dispatch_force_end_command_logs_missing_id(logger, tmp_path):
	"""_handle logs an error when conversation_id is absent from the command dict."""
	from server.gateway.dispatch import dispatch_force_end_commands
	from server.registry import Registry
	import json

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = Registry()
	backend = MagicMock()

	registered_handler = None

	async def fake_start_listener(handler):
		nonlocal registered_handler
		registered_handler = handler

	backend.start_force_end_command_listener = fake_start_listener

	await dispatch_force_end_commands(registry, backend, logger, supervisor=None)
	assert registered_handler is not None

	await registered_handler({"issued_at": "2026-05-25T00:00:00+00:00"})

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert any("force_end_command_missing_id" in e["detail"] for e in errors)
