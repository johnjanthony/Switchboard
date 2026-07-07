"""Verify that state-mutating handlers/helpers also issue Firebase writes
under the new /conversations/<id>/... schema."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, call


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_store_backend():
	"""Return a MagicMock with all ConversationStore methods as AsyncMocks."""
	backend = MagicMock()
	# write_conversation_message: expanded form returns (correlation, msg_id);
	# dict form returns push-key string. Mock returns the expanded-form tuple so
	# handlers that unpack correlation+msg_id don't error.
	backend.write_conversation_message = AsyncMock(return_value=(("conv-x", "system"), "msg-0"))
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_member = AsyncMock()
	backend.remove_conversation_member = AsyncMock()
	backend.set_conversation_state = AsyncMock()
	backend.set_conversation_last_activity = AsyncMock()
	backend.set_session_home = AsyncMock()
	backend.set_global_away_mode = AsyncMock()
	backend.send_timeout_followup = AsyncMock()
	backend.send_resolution_confirmation = AsyncMock()
	backend.mark_question_cancelled = AsyncMock()
	backend.write_agent_status = AsyncMock()
	backend.write_conversation_member_history = AsyncMock()
	backend.add_pending_question_record = AsyncMock()
	backend.remove_pending_question_record = AsyncMock()
	return backend


def _make_config(tmp_path):
	from server.config import Config
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "log.jsonl"),
	)


def _make_logger(cfg):
	from server.logging_jsonl import JsonlLogger
	return JsonlLogger(cfg.log_path)


def _make_active_conv(conv_id: str = "conv-abc", session_id: str = "s-1", sender: str = "Claude"):
	"""Return (registry, conv) with one alive member bound to conv_id."""
	from server.registry import Conversation, ConversationMember, Registry
	r = Registry()
	conv = Conversation(id=conv_id, title="test conv")
	conv.created_at = time.time()
	conv.last_activity_at = conv.created_at
	m = ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd="C:/Work/X",
		surface="windows",
		joined_at=time.time(),
	)
	conv.members_active[session_id] = m
	r.conversations[conv_id] = conv
	r.bind_session(session_id, conv_id)
	r.set_session_home(session_id, conv_id)
	return r, conv


async def _drain_bg():
	"""Yield control so _spawn_bg tasks get a chance to run."""
	for _ in range(5):
		await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Test 2: leave_conversation removes member, writes parting message, sets state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_leave_conversation_removes_member_and_writes_state(tmp_path):
	from server.gateway import build_tool_handlers

	cfg = _make_config(tmp_path)
	logger = _make_logger(cfg)
	backend = _make_store_backend()
	registry, conv = _make_active_conv()

	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.leave_conversation("Claude", "goodbye!", cli_session_id="s-1", cwd="C:/Work/X")

	assert json.loads(result) == {"status": "ok", "conversation_id": conv.id}
	await _drain_bg()

	# Member was removed
	backend.remove_conversation_member.assert_awaited_once_with(conv.id, "Claude")
	# Parting message written to new schema via expanded-form call
	backend.write_conversation_message.assert_awaited()
	# Expanded form: (conv_id, sender, type, text, ...)
	call_args = backend.write_conversation_message.call_args[0]
	assert call_args[0] == conv.id
	assert call_args[1] == "Claude"  # sender
	assert call_args[2] == "parting"  # type
	assert call_args[3] == "goodbye!"  # text
	# Conversation ended (sole member left)
	backend.set_conversation_state.assert_awaited_once_with(conv.id, "ended")
	backend.set_conversation_last_activity.assert_awaited()


# ---------------------------------------------------------------------------
# Test 3: combine writes target member and source state=ended
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_combine_writes_both_target_member_and_source_state(tmp_path):
	from server.conversation_ops import _perform_combine
	from server.registry import Conversation, ConversationMember, Registry

	backend = _make_store_backend()
	registry = Registry()
	for cid in ("conv-src", "conv-tgt"):
		conv = Conversation(id=cid, title=cid)
		conv.created_at = time.time()
		conv.last_activity_at = conv.created_at
		registry.conversations[cid] = conv
	m = ConversationMember(
		cli_session_id="s-1",
		sender="Agent",
		cwd="C:/Work/X",
		surface="windows",
		joined_at=time.time(),
	)
	registry.conversations["conv-src"].members_active["s-1"] = m
	registry.bind_session("s-1", "conv-src")

	result = await _perform_combine(registry, "conv-src", "conv-tgt", None, None, backend=backend)
	assert "ok" in result

	await _drain_bg()

	backend.remove_conversation_member.assert_awaited_with("conv-src", "Agent")
	backend.write_conversation_member.assert_awaited()
	backend.set_conversation_state.assert_awaited_once_with("conv-src", "ended")
	backend.set_conversation_last_activity.assert_awaited_once_with("conv-tgt", pytest.approx(time.time(), abs=5))


# ---------------------------------------------------------------------------
# Test 5: handle_session_end writes dormant member state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_session_end_writes_dormant_member_state():
	from server.cli_session_end import handle_session_end
	from server.registry import Conversation, ConversationMember, Registry

	backend = _make_store_backend()
	registry = Registry()
	conv = Conversation(id="conv-x", title="test")
	m = ConversationMember(
		cli_session_id="s-1",
		sender="Claude",
		cwd="C:/X",
		surface="windows",
		joined_at=time.time(),
	)
	conv.members_active["s-1"] = m
	registry.conversations["conv-x"] = conv
	registry.bind_session("s-1", "conv-x")

	await handle_session_end(registry, "s-1", "logout", now=lambda: "2026-01-01T00:00:00+00:00", backend=backend)
	await _drain_bg()

	# Member marked dormant
	assert m.alive is False
	# Firebase write with alive=False
	backend.write_conversation_member.assert_awaited_once_with("conv-x", m)
	backend.write_conversation_message.assert_awaited()
	msg_arg = backend.write_conversation_message.call_args[0][1]
	assert "dormant" in msg_arg["text"]


# ---------------------------------------------------------------------------
# Test 6: apply_fallback create_new writes conversation_meta
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_fallback_create_new_writes_conversation_meta():
	from server.session_fallback import apply_fallback
	from server.registry import Registry

	backend = _make_store_backend()
	registry = Registry()
	registry.global_away_mode = True
	# Realistic create_new scenario: session is still alive/bound (e.g. peer
	# just force-ended the shared conv from under it). No home pointer set, so
	# compute_fallback returns create_new. The session being bound is what
	# distinguishes this from the dormant short-circuit added in Fix Pack 4.
	registry.bind_session("s-orphan", "conv-already-gone")

	apply_fallback(registry, "s-orphan", backend=backend)
	await _drain_bg()

	new_conv_id = registry.session_to_conversation_id.get("s-orphan")
	assert new_conv_id is not None
	assert new_conv_id != "conv-already-gone"
	backend.write_conversation_meta.assert_awaited_once()
	call_kwargs = backend.write_conversation_meta.call_args
	assert call_kwargs[0][0] == new_conv_id
	assert call_kwargs[1]["state"] == "active"
	backend.set_session_home.assert_awaited_once_with("s-orphan", new_conv_id)


# ---------------------------------------------------------------------------
# Test 7: message_and_await_agent writes message to conversations path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_and_await_writes_message_to_conversations_path(tmp_path):
	from server.gateway import build_tool_handlers
	from server.registry import Conversation, ConversationMember, Registry

	cfg = _make_config(tmp_path)
	logger = _make_logger(cfg)
	backend = _make_store_backend()

	# Two-member conv so message_and_await_agent doesn't immediately return __CONVERSATION_EMPTY__
	registry = Registry()
	conv = Conversation(id="conv-m", title="collab")
	conv.created_at = time.time()
	conv.last_activity_at = conv.created_at
	for sid, name in [("s-1", "Alice"), ("s-2", "Bob")]:
		m = ConversationMember(
			cli_session_id=sid,
			sender=name,
			cwd="C:/X",
			surface="windows",
			joined_at=time.time(),
		)
		conv.members_active[sid] = m
		registry.bind_session(sid, "conv-m")
	registry.conversations["conv-m"] = conv

	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Start message_and_await_agent as a task so it can block waiting for Bob
	task = asyncio.create_task(
		handlers.message_and_await_agent("Alice", "hello!", cli_session_id="s-1", cwd="C:/X")
	)
	# Let it run until it blocks
	await asyncio.sleep(0)
	await _drain_bg()

	backend.write_conversation_message.assert_awaited()
	# Expanded form: (conv_id, sender, type, text, ...)
	call_args = backend.write_conversation_message.call_args[0]
	assert call_args[1] == "Alice"  # sender
	assert call_args[2] == "agent_msg"  # type
	assert call_args[3] == "hello!"  # text
	backend.set_conversation_last_activity.assert_awaited()

	task.cancel()
	try:
		await task
	except (asyncio.CancelledError, Exception):
		pass


# ---------------------------------------------------------------------------
# Test 8: _create_active_conversation_for writes meta and member
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_active_conversation_for_writes_meta_and_member():
	from server.conversation_ops import _create_active_conversation_for
	from server.registry import Registry

	backend = _make_store_backend()
	registry = Registry()

	conv_id = await _create_active_conversation_for(
		registry, "s-new", "C:/Work/Project", "Claude", backend=backend
	)
	await _drain_bg()

	assert conv_id in registry.conversations
	backend.write_conversation_meta.assert_awaited_once()
	call_kwargs = backend.write_conversation_meta.call_args
	assert call_kwargs[0][0] == conv_id
	assert call_kwargs[1]["state"] == "active"
	backend.write_conversation_member.assert_awaited_once()
	member_arg = backend.write_conversation_member.call_args[0][1]
	assert member_arg.sender == "Claude"
	backend.set_session_home.assert_awaited_once_with("s-new", conv_id)


# ---------------------------------------------------------------------------
# Test 9: handle_fresh (spawn) writes conversation_meta and system message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_handle_fresh_writes_conversation_meta_and_message(tmp_path, monkeypatch):
	from server.spawn import SpawnHandler
	from server.config import Config
	from server.registry import Registry

	(tmp_path / "myproject").mkdir()
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60.0,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=tmp_path,
	)
	logger = _make_logger(cfg)
	backend = _make_store_backend()
	registry = Registry()
	registry.global_away_mode = True

	monkeypatch.setattr(SpawnHandler, "_user_has_interactive_session", AsyncMock(return_value=True))
	monkeypatch.setattr(SpawnHandler, "_invoke_launcher", AsyncMock())

	handler = SpawnHandler(cfg, backend, logger, registry)
	await handler.handle_fresh({
		"type": "fresh",
		"surface": "windows",
		"project": "myproject",
		"prompt": None,
		"target_conversation_id": None,
		"issued_at": "2026-01-01T00:00:00+00:00",
	})
	await _drain_bg()

	backend.write_conversation_meta.assert_awaited_once()
	meta_kwargs = backend.write_conversation_meta.call_args[1]
	assert meta_kwargs["state"] == "active"
	assert meta_kwargs["continued_from"] is None
	backend.write_conversation_message.assert_awaited_once()
	msg_arg = backend.write_conversation_message.call_args[0][1]
	assert "Spawning Claude" in msg_arg["text"]


# ---------------------------------------------------------------------------
# Test 10: handle_force_end with no backend still works (backward compat)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_force_end_without_backend_still_ends_conversation():
	from server.gateway.dispatch import handle_force_end
	from server.registry import Conversation, ConversationMember, Registry

	registry = Registry()
	conv = Conversation(id="conv-1", title="test")
	m = ConversationMember(cli_session_id="s-A", sender="A", cwd="C:/X", surface="windows", joined_at=0.0)
	conv.members_active["s-A"] = m
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-A", "conv-1")

	# Call without backend — must not raise
	await handle_force_end(registry, "conv-1")
	assert conv.state == "ended"
