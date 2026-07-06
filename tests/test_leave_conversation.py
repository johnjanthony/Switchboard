"""Tests for leave_conversation."""

from __future__ import annotations

import asyncio
import time

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _make_member(session_id: str, sender: str, cwd: str = "C:/X", alive: bool = True) -> ConversationMember:
	return ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=time.time(),
		alive=alive,
	)


def _single_member_registry() -> tuple[Registry, str]:
	"""Registry with one active member in one conversation."""
	r = Registry()
	m = _make_member("s-solo", "Claude-Solo")
	conv = Conversation(id="conv-solo", title="solo conv")
	conv.members_active["s-solo"] = m
	r.conversations["conv-solo"] = conv
	r.bind_session("s-solo", "conv-solo")
	r.set_session_home("s-solo", "conv-solo")
	return r, "conv-solo"


def _two_member_registry() -> tuple[Registry, str]:
	"""Registry with two alive members in one conversation."""
	r = Registry()
	mA = _make_member("s-A", "Claude-A")
	mB = _make_member("s-B", "Claude-B", cwd="C:/Y")
	conv = Conversation(id="conv-duo", title="duo conv")
	conv.members_active["s-A"] = mA
	conv.members_active["s-B"] = mB
	r.conversations["conv-duo"] = conv
	r.bind_session("s-A", "conv-duo")
	r.bind_session("s-B", "conv-duo")
	r.set_session_home("s-A", "conv-duo")
	r.set_session_home("s-B", "conv-duo")
	return r, "conv-duo"


def _alive_plus_dormant_registry() -> tuple[Registry, str]:
	"""Registry with one alive member (A) and one dormant member (B)."""
	r = Registry()
	mA = _make_member("s-A", "Claude-A")
	mB = _make_member("s-B", "Claude-B", cwd="C:/Y", alive=False)
	conv = Conversation(id="conv-mixed", title="mixed conv")
	conv.members_active["s-A"] = mA
	conv.members_active["s-B"] = mB
	r.conversations["conv-mixed"] = conv
	r.bind_session("s-A", "conv-mixed")
	r.bind_session("s-B", "conv-mixed")
	r.set_session_home("s-A", "conv-mixed")
	r.set_session_home("s-B", "conv-mixed")
	return r, "conv-mixed"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_leave_unbound_session_errors(cfg, logger):
	"""Session not bound to any conversation returns an error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.leave_conversation(
		"Claude-X",
		"goodbye",
		cli_session_id="s-unbound",
		cwd="C:/X",
	)

	assert "ERROR" in result
	assert "not in any conversation" in result


@pytest.mark.asyncio
async def test_leave_appends_parting_and_removes_member(cfg, logger):
	"""leave_conversation appends a parting message and moves member to history."""
	backend = RecordingBackend()
	registry, conv_id = _two_member_registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.leave_conversation(
		"Claude-A",
		"signing off",
		cli_session_id="s-A",
		cwd="C:/X",
	)

	assert "ok" in result
	conv = registry.conversations[conv_id]
	# Member moved to history
	assert "s-A" not in conv.members_active
	assert any(m.sender == "Claude-A" for m in conv.members_history)
	# Parting message appended
	parting_msgs = [m for m in conv.messages if m.get("type") == "parting"]
	assert len(parting_msgs) == 1
	assert parting_msgs[0]["text"] == "signing off"
	assert parting_msgs[0]["sender"] == "Claude-A"


@pytest.mark.asyncio
async def test_leave_last_alive_no_dormant_ends_conversation(cfg, logger):
	"""Single-member conv: member leaves → conversation transitions to ended."""
	backend = RecordingBackend()
	registry, conv_id = _single_member_registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.leave_conversation(
		"Claude-Solo",
		"last one out",
		cli_session_id="s-solo",
		cwd="C:/X",
	)

	assert "ok" in result
	conv = registry.conversations[conv_id]
	assert conv.state == "ended"
	assert conv.ended_at is not None


@pytest.mark.asyncio
async def test_leave_last_alive_with_dormant_keeps_conversation_active(cfg, logger):
	"""Alive A leaves; dormant B remains → conversation stays Active."""
	backend = RecordingBackend()
	registry, conv_id = _alive_plus_dormant_registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.leave_conversation(
		"Claude-A",
		"heading out",
		cli_session_id="s-A",
		cwd="C:/X",
	)

	assert "ok" in result
	conv = registry.conversations[conv_id]
	assert conv.state == "active"


@pytest.mark.asyncio
async def test_leave_applies_session_fallback_unbind_when_away_off(cfg, logger):
	"""With global_away_mode=False, session is unbound after leaving."""
	backend = RecordingBackend()
	registry, conv_id = _single_member_registry()
	registry.global_away_mode = False
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	await handlers.leave_conversation(
		"Claude-Solo",
		"done",
		cli_session_id="s-solo",
		cwd="C:/X",
	)

	# Session should be unbound (fallback=unbind when away is off)
	assert "s-solo" not in registry.session_to_conversation_id


@pytest.mark.asyncio
async def test_leave_applies_session_fallback_rebind_when_away_on_home_active(cfg, logger):
	"""With global_away_mode=True and an active home conversation, session rebinds to home."""
	backend = RecordingBackend()
	registry, _conv_id = _two_member_registry()

	# Create a separate home conversation for s-A
	from server.registry import Conversation
	home_conv = Conversation(id="conv-home", title="home")
	registry.conversations["conv-home"] = home_conv
	registry.set_session_home("s-A", "conv-home")
	registry.bind_session("s-A", "conv-duo")  # re-bind to duo (set_session_home may have overwritten)
	registry.global_away_mode = True

	handlers = build_tool_handlers(cfg, registry, backend, logger)

	await handlers.leave_conversation(
		"Claude-A",
		"bye from duo",
		cli_session_id="s-A",
		cwd="C:/X",
	)

	# After fallback with away_mode=True and active home, session should rebind to home
	assert registry.session_to_conversation_id.get("s-A") == "conv-home"


@pytest.mark.asyncio
async def test_leave_missing_cli_session_id_errors(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.leave_conversation("Claude-X", "bye")

	assert result.startswith("ERROR: cli_session_id required")
