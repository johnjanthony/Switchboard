"""End-to-end test: combine_conversations with mixed alive + dormant members."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)


def _make_member(
	session_id: str,
	sender: str,
	cwd: str = "C:/Work/X",
	alive: bool = True,
	session_lost_permanently: bool = False,
) -> ConversationMember:
	return ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=time.time(),
		alive=alive,
		session_lost_permanently=session_lost_permanently,
	)


# ---------------------------------------------------------------------------
# Test 1: combine conv-A (1 alive + 1 dormant) into conv-B (1 alive)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_combine_alive_and_dormant(tmp_path):
	"""Conv-A has 1 alive + 1 dormant member. Conv-B has 1 alive member.
	Combine A into B. Verify:
	- Alive A member's session rebound to B
	- Dormant A member's session pre-bound to B
	- A spawn-pending JSON exists for the dormant member
	- Source A is Ended
	- Target B has system message about merge"""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Conv-A: one alive + one dormant member
	conv_a = Conversation(id="conv-a", title="Conv A")
	m_alive_a = _make_member("s-alive-a", "claude-alive-a", alive=True)
	m_dormant_a = _make_member("s-dormant-a", "claude-dormant-a", alive=False)
	conv_a.members_active["claude-alive-a"] = m_alive_a
	conv_a.members_active["claude-dormant-a"] = m_dormant_a
	registry.conversations["conv-a"] = conv_a
	registry.bind_session("s-alive-a", "conv-a")
	registry.bind_session("s-dormant-a", "conv-a")

	# Conv-B: one alive member (the caller)
	conv_b = Conversation(id="conv-b", title="Conv B")
	m_b = _make_member("s-b", "claude-b", alive=True)
	conv_b.members_active["claude-b"] = m_b
	registry.conversations["conv-b"] = conv_b
	registry.bind_session("s-b", "conv-b")
	registry.set_session_home("s-b", "conv-b")

	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.combine_conversations(
		"conv-a",
		"conv-b",
		cli_session_id="s-b",
		cwd="C:/Work/X",
	)

	assert result.startswith("ok"), f"Unexpected result: {result}"

	# Source conv-A is ended
	assert conv_a.state == "ended"
	assert conv_a.ended_at is not None

	# Alive A member's session rebound to B
	assert registry.session_to_conversation_id["s-alive-a"] == "conv-b"
	assert "claude-alive-a" in conv_b.members_active

	# Dormant A member's session pre-bound to B
	assert registry.session_to_conversation_id["s-dormant-a"] == "conv-b"
	assert "claude-dormant-a" in conv_b.members_active

	# Dormant member still dormant in B
	assert not conv_b.members_active["claude-dormant-a"].alive

	# Spawn-pending file written for the dormant member
	pending_files = list(tmp_path.glob("spawn-pending-*.json"))
	assert len(pending_files) == 1, f"Expected 1 pending file, got: {[f.name for f in pending_files]}"
	payload = json.loads(pending_files[0].read_text(encoding="utf-8"))
	assert payload["type"] == "combine_resume"
	assert payload["target_conversation_id"] == "conv-b"
	assert payload["source_conversation_id"] == "conv-a"
	assert len(payload["agents"]) == 1
	assert payload["agents"][0]["cli_session_id"] == "s-dormant-a"

	# Target B has system message about merge
	sys_msgs = [m for m in conv_b.messages if m.get("type") == "system"]
	assert any("Merged with" in m["text"] and "Conv A" in m["text"] for m in sys_msgs), \
		f"Expected merge system message in B; messages: {[m['text'] for m in sys_msgs]}"


# ---------------------------------------------------------------------------
# Test 2: combine with target having a waiter → blocked member wakes with merge message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_combine_with_target_having_waiter(tmp_path):
	"""Conv-B has a member blocked in wait_queue (from message_and_await_agent).
	Combine A (one alive member) into B.
	Verify the blocked B member wakes and sees the merge system message."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Conv-A: one alive member
	conv_a = Conversation(id="conv-a2", title="Source A2")
	m_a = _make_member("s-a2", "claude-a2", alive=True)
	conv_a.members_active["claude-a2"] = m_a
	registry.conversations["conv-a2"] = conv_a
	registry.bind_session("s-a2", "conv-a2")
	registry.set_session_home("s-a2", "conv-a2")

	# Conv-B: two alive members — one will be the waiter, one will perform the combine
	conv_b = Conversation(id="conv-b2", title="Target B2")
	m_b_waiter = _make_member("s-b2-waiter", "claude-b2-waiter", alive=True)
	m_b_combiner = _make_member("s-b2-combiner", "claude-b2-combiner", alive=True)
	conv_b.members_active["claude-b2-waiter"] = m_b_waiter
	conv_b.members_active["claude-b2-combiner"] = m_b_combiner
	registry.conversations["conv-b2"] = conv_b
	registry.bind_session("s-b2-waiter", "conv-b2")
	registry.bind_session("s-b2-combiner", "conv-b2")
	registry.set_session_home("s-b2-combiner", "conv-b2")

	# The waiter speaks first to enqueue itself
	task_waiter_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-b2-waiter",
			message="I am waiting in B for something to happen",
			cli_session_id="s-b2-waiter",
			cwd="C:/Work/B2",
		)
	)
	await asyncio.sleep(0.05)

	# Verify waiter is in wait_queue
	assert len(conv_b.wait_queue) == 1

	# Combiner performs combine — this should wake the waiter
	result = await handlers.combine_conversations(
		"conv-a2",
		"conv-b2",
		cli_session_id="s-b2-combiner",
		cwd="C:/Work/B2",
	)
	assert result.startswith("ok"), f"Unexpected result: {result}"

	# Blocked waiter should wake with merge message
	wake_result = await asyncio.wait_for(task_waiter_speak, timeout=2.0)
	assert "Merged with" in wake_result or "joined via combine" in wake_result, \
		f"Expected merge indicator in waiter's wake payload; got: {wake_result!r}"


# ---------------------------------------------------------------------------
# Test 3: combine then speak — A-member-now-in-B routes correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_combine_then_speak(tmp_path):
	"""After combining A into B, the moved A member (now in B) calls
	message_and_await_agent and the message routes correctly within B."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Conv-A: one alive member
	conv_a = Conversation(id="conv-a3", title="Source A3")
	m_a = _make_member("s-a3", "claude-a3", alive=True)
	conv_a.members_active["claude-a3"] = m_a
	registry.conversations["conv-a3"] = conv_a
	registry.bind_session("s-a3", "conv-a3")
	registry.set_session_home("s-a3", "conv-a3")

	# Conv-B: two alive members — one is the combiner, one is the eventual speaker
	conv_b = Conversation(id="conv-b3", title="Target B3")
	m_b_combiner = _make_member("s-b3-combiner", "claude-b3-combiner", alive=True)
	m_b_listener = _make_member("s-b3-listener", "claude-b3-listener", alive=True)
	conv_b.members_active["claude-b3-combiner"] = m_b_combiner
	conv_b.members_active["claude-b3-listener"] = m_b_listener
	registry.conversations["conv-b3"] = conv_b
	registry.bind_session("s-b3-combiner", "conv-b3")
	registry.bind_session("s-b3-listener", "conv-b3")
	registry.set_session_home("s-b3-combiner", "conv-b3")

	# Combine A into B
	result = await handlers.combine_conversations(
		"conv-a3",
		"conv-b3",
		cli_session_id="s-b3-combiner",
		cwd="C:/Work/B3",
	)
	assert result.startswith("ok"), f"Unexpected result: {result}"

	# Verify A3 is now in B3
	assert registry.session_to_conversation_id["s-a3"] == "conv-b3"
	assert "claude-a3" in conv_b.members_active

	# Now the moved A member speaks in B — should work without errors
	# The listener in B will be woken by this message (A's speak enqueues A, listener wakes)
	task_a3_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-a3",
			message="hello from moved claude-a3",
			cli_session_id="s-a3",
			cwd="C:/Work/X",
		)
	)
	await asyncio.sleep(0.05)

	# B's listener speaks back — wakes A3
	task_b3_listener_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-b3-listener",
			message="welcome claude-a3 to conv-b3",
			cli_session_id="s-b3-listener",
			cwd="C:/Work/B3",
		)
	)

	result_a3 = await asyncio.wait_for(task_a3_speak, timeout=2.0)
	assert "welcome claude-a3 to conv-b3" in result_a3, \
		f"Moved A3 member should see B3 listener's message; got: {result_a3!r}"

	task_b3_listener_speak.cancel()
	try:
		await task_b3_listener_speak
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Test 4: combine clears open pointer when source was open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_combine_clears_open_when_source_was_open(tmp_path):
	"""If the source conv was the openConversationId, after combine the pointer is cleared."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Conv-A: source (was open)
	conv_a = Conversation(id="conv-a4", title="Source A4")
	m_a = _make_member("s-a4", "claude-a4", alive=True)
	conv_a.members_active["claude-a4"] = m_a
	registry.conversations["conv-a4"] = conv_a
	registry.bind_session("s-a4", "conv-a4")
	registry.set_session_home("s-a4", "conv-a4")
	registry.open_conversation_id = "conv-a4"  # source is open

	# Conv-B: target
	conv_b = Conversation(id="conv-b4", title="Target B4")
	m_b = _make_member("s-b4", "claude-b4", alive=True)
	conv_b.members_active["claude-b4"] = m_b
	registry.conversations["conv-b4"] = conv_b
	registry.bind_session("s-b4", "conv-b4")
	registry.set_session_home("s-b4", "conv-b4")

	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.combine_conversations(
		"conv-a4",
		"conv-b4",
		cli_session_id="s-b4",
		cwd="C:/Work/X",
	)

	assert result.startswith("ok"), f"Unexpected result: {result}"
	assert registry.open_conversation_id is None, \
		"open pointer must be cleared when source was open"
	assert conv_a.state == "ended"
