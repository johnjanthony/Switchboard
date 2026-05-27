"""Tests for open_conversation."""

from __future__ import annotations

import asyncio

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _make_registry_with_bound_session(session_id="s-1", conv_id="conv-abc", sender="Claude-A", cwd="C:/Work/X"):
	"""Registry with one conversation containing one member, session bound."""
	r = Registry()
	conv = Conversation(id=conv_id, title="original title")
	m = ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=0.0,
	)
	conv.members_active[sender] = m
	r.conversations[conv_id] = conv
	r.bind_session(session_id, conv_id)
	return r, conv_id


@pytest.mark.asyncio
async def test_open_conversation_sets_pointer(cfg, logger):
	"""open_conversation sets registry.open_conversation_id to the caller's conv."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-A",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	assert result == f"ok. open_conversation = {conv_id}"
	assert r.open_conversation_id == conv_id


@pytest.mark.asyncio
async def test_open_conversation_updates_title(cfg, logger):
	"""Providing title updates conv.title."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	await handlers.open_conversation(
		"Claude-A",
		title="New Title",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	assert r.conversations[conv_id].title == "New Title"


@pytest.mark.asyncio
async def test_open_conversation_replaces_prior_open(cfg, logger):
	"""open_conversation flips the open pointer from one conv to another."""
	backend = RecordingBackend()
	r = Registry()

	# First conv — currently open
	conv1 = Conversation(id="conv-1", title="first")
	m1 = ConversationMember(cli_session_id="s-1", sender="Claude-A", cwd="C:/X", surface="windows", joined_at=0.0)
	conv1.members_active["Claude-A"] = m1
	r.conversations["conv-1"] = conv1
	r.bind_session("s-1", "conv-1")
	r.open_conversation_id = "conv-1"

	# Second conv
	conv2 = Conversation(id="conv-2", title="second")
	m2 = ConversationMember(cli_session_id="s-2", sender="Claude-B", cwd="C:/Y", surface="windows", joined_at=0.0)
	conv2.members_active["Claude-B"] = m2
	r.conversations["conv-2"] = conv2
	r.bind_session("s-2", "conv-2")

	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-B",
		cli_session_id="s-2",
		cwd="C:/Y",
	)

	assert result == "ok. open_conversation = conv-2"
	assert r.open_conversation_id == "conv-2"


@pytest.mark.asyncio
async def test_open_conversation_mints_when_unbound(cfg, logger):
	"""Session not bound to any conversation: open_conversation mints one and promotes it.

	Lets agents bootstrap a collab without first sending a real ask/notify.
	"""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-X",
		cli_session_id="s-unbound",
		cwd="C:/Work/X",
	)

	assert result.startswith("ok. open_conversation = ")
	conv_id = result.removeprefix("ok. open_conversation = ")
	assert conv_id.startswith("conv-")
	assert r.open_conversation_id == conv_id
	assert conv_id in r.conversations
	conv = r.conversations[conv_id]
	assert "Claude-X" in conv.members_active
	assert conv.members_active["Claude-X"].cli_session_id == "s-unbound"
	assert r.session_to_conversation_id["s-unbound"] == conv_id


@pytest.mark.asyncio
async def test_open_conversation_unbound_honors_title(cfg, logger):
	"""When minting on unbound open, the supplied title is used."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-X",
		title="Collab bootstrap",
		cli_session_id="s-unbound",
		cwd="C:/Work/X",
	)

	conv_id = result.removeprefix("ok. open_conversation = ")
	assert r.conversations[conv_id].title == "Collab bootstrap"


@pytest.mark.asyncio
async def test_open_conversation_renames_member_when_sender_changes(cfg, logger):
	"""Member currently keyed by OldName; calling with sender=NewName renames the key."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session(session_id="s-1", sender="OldName")
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"NewName",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	conv = r.conversations[conv_id]
	assert "NewName" in conv.members_active
	assert "OldName" not in conv.members_active
	assert conv.members_active["NewName"].sender == "NewName"
	assert result.startswith("ok.")


@pytest.mark.asyncio
async def test_open_conversation_no_rename_when_sender_unchanged(cfg, logger):
	"""When sender matches existing key, members_active is unchanged (no spurious rename)."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session(session_id="s-1", sender="Claude-A")
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-A",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	conv = r.conversations[conv_id]
	assert "Claude-A" in conv.members_active
	assert len(conv.members_active) == 1
	assert result.startswith("ok.")


@pytest.mark.asyncio
async def test_open_conversation_errors_missing_cli_session_id(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-A",
		cwd="C:/Work/X",
	)

	assert result.startswith("ERROR: cli_session_id required")
