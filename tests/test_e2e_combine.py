"""End-to-end test: combine_conversations with mixed alive + dormant members."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
	- Dormant A member's session bound to B and member flipped alive (relaunch in flight)
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
	conv_a.members_active["s-alive-a"] = m_alive_a
	conv_a.members_active["s-dormant-a"] = m_dormant_a
	registry.conversations["conv-a"] = conv_a
	registry.bind_session("s-alive-a", "conv-a")
	registry.bind_session("s-dormant-a", "conv-a")

	# Conv-B: one alive member (the caller)
	conv_b = Conversation(id="conv-b", title="Conv B")
	m_b = _make_member("s-b", "claude-b", alive=True)
	conv_b.members_active["s-b"] = m_b
	registry.conversations["conv-b"] = conv_b
	registry.bind_session("s-b", "conv-b")
	registry.set_session_home("s-b", "conv-b")

	handlers = build_tool_handlers(cfg, registry, backend, logger)

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
			patch("server.spawn.invoke_spawn_launcher", AsyncMock()) as mock_launch:
		result = await handlers.combine_conversations(
			"conv-a",
			"conv-b",
			cli_session_id="s-b",
			cwd="C:/Work/X",
		)

	assert json.loads(result)["status"] == "ok", f"Unexpected result: {result}"

	# Source conv-A is ended
	assert conv_a.state == "ended"
	assert conv_a.ended_at is not None

	# Alive A member's session rebound to B
	assert registry.session_to_conversation_id["s-alive-a"] == "conv-b"
	assert "s-alive-a" in conv_b.members_active

	# Dormant A member's session pre-bound to B
	assert registry.session_to_conversation_id["s-dormant-a"] == "conv-b"
	assert "s-dormant-a" in conv_b.members_active

	# Dormant member flipped alive in B (relaunch in flight) and launcher fired
	assert conv_b.members_active["s-dormant-a"].alive
	mock_launch.assert_awaited_once()

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
	conv_a.members_active["s-a2"] = m_a
	registry.conversations["conv-a2"] = conv_a
	registry.bind_session("s-a2", "conv-a2")
	registry.set_session_home("s-a2", "conv-a2")

	# Conv-B: two alive members — one will be the waiter, one will perform the combine
	conv_b = Conversation(id="conv-b2", title="Target B2")
	m_b_waiter = _make_member("s-b2-waiter", "claude-b2-waiter", alive=True)
	m_b_combiner = _make_member("s-b2-combiner", "claude-b2-combiner", alive=True)
	conv_b.members_active["s-b2-waiter"] = m_b_waiter
	conv_b.members_active["s-b2-combiner"] = m_b_combiner
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
	assert json.loads(result)["status"] == "ok", f"Unexpected result: {result}"

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
	conv_a.members_active["s-a3"] = m_a
	registry.conversations["conv-a3"] = conv_a
	registry.bind_session("s-a3", "conv-a3")
	registry.set_session_home("s-a3", "conv-a3")

	# Conv-B: two alive members — one is the combiner, one is the eventual speaker
	conv_b = Conversation(id="conv-b3", title="Target B3")
	m_b_combiner = _make_member("s-b3-combiner", "claude-b3-combiner", alive=True)
	m_b_listener = _make_member("s-b3-listener", "claude-b3-listener", alive=True)
	conv_b.members_active["s-b3-combiner"] = m_b_combiner
	conv_b.members_active["s-b3-listener"] = m_b_listener
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
	assert json.loads(result)["status"] == "ok", f"Unexpected result: {result}"

	# Verify A3 is now in B3
	assert registry.session_to_conversation_id["s-a3"] == "conv-b3"
	assert "s-a3" in conv_b.members_active

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
async def test_combine_migrates_source_waiter_to_target(tmp_path):
	"""Conv-A (source) has a member blocked in wait_queue. Combine A into B.
	The blocked source-side waiter must NOT be stranded — its wait_entry should
	either be migrated to target.wait_queue or already woken by the post-combine
	_wake_one_from(target). Verify that a subsequent message in target delivers
	to the previously-blocked agent (no 24h strand)."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Conv-A (source): two alive members — Alice will be the blocked waiter, Bob is also in source
	conv_a = Conversation(id="conv-a5", title="Source A5")
	m_alice = _make_member("s-alice", "claude-alice", alive=True)
	m_bob = _make_member("s-bob", "claude-bob", alive=True)
	conv_a.members_active["s-alice"] = m_alice
	conv_a.members_active["s-bob"] = m_bob
	registry.conversations["conv-a5"] = conv_a
	registry.bind_session("s-alice", "conv-a5")
	registry.bind_session("s-bob", "conv-a5")
	registry.set_session_home("s-alice", "conv-a5")
	registry.set_session_home("s-bob", "conv-a5")

	# Conv-B (target): one alive member, the combiner
	conv_b = Conversation(id="conv-b5", title="Target B5")
	m_combiner = _make_member("s-combiner5", "claude-combiner5", alive=True)
	conv_b.members_active["s-combiner5"] = m_combiner
	registry.conversations["conv-b5"] = conv_b
	registry.bind_session("s-combiner5", "conv-b5")
	registry.set_session_home("s-combiner5", "conv-b5")

	# Alice blocks in source's wait_queue
	task_alice = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-alice",
			message="Alice waiting in source",
			cli_session_id="s-alice",
			cwd="C:/Work/A5",
		)
	)
	await asyncio.sleep(0.05)

	# Confirm Alice is enqueued in source's wait_queue
	assert len(conv_a.wait_queue) == 1, \
		f"Expected 1 waiter in source; got {len(conv_a.wait_queue)}"

	# Combiner combines source into target
	result = await handlers.combine_conversations(
		"conv-a5",
		"conv-b5",
		cli_session_id="s-combiner5",
		cwd="C:/Work/B5",
	)
	assert json.loads(result)["status"] == "ok", f"Unexpected result: {result}"

	# Source.wait_queue must be empty post-combine (migrated or drained)
	assert len(conv_a.wait_queue) == 0, \
		f"source.wait_queue must be empty after combine; still has {len(conv_a.wait_queue)} entries"

	# Alice was migrated to target
	assert "s-alice" in conv_b.members_active
	assert registry.session_to_conversation_id["s-alice"] == "conv-b5"

	# At this point Alice's wait_entry is either:
	#   (a) already resolved by _wake_one_from(target) at line 488, or
	#   (b) sitting in target.wait_queue awaiting the next speaker.
	# Either way, Alice must NOT remain blocked indefinitely.
	if not task_alice.done():
		# Bob (now also in target) speaks; this must wake Alice via target's wait_queue
		assert "s-bob" in conv_b.members_active
		task_bob = asyncio.create_task(
			handlers.message_and_await_agent(
				"claude-bob",
				message="Bob speaking in merged conv",
				cli_session_id="s-bob",
				cwd="C:/Work/A5",
			)
		)
		try:
			alice_result = await asyncio.wait_for(task_alice, timeout=2.0)
		finally:
			task_bob.cancel()
			try:
				await task_bob
			except asyncio.CancelledError:
				pass
		assert alice_result, "Alice's future must resolve with a payload, not stay pending"
		# Payload should reflect either the merge marker or Bob's message
		assert ("Merged" in alice_result
				or "joined via combine" in alice_result
				or "Bob speaking" in alice_result), \
			f"Alice's wake payload should reflect merge or Bob's message; got: {alice_result!r}"
	else:
		# Already woken by _wake_one_from(target) — verify a sensible payload
		alice_result = await task_alice
		assert alice_result, "Alice's resolved payload must be non-empty"
		assert "__TIMEOUT__" not in alice_result, \
			f"Alice must not have timed out; got: {alice_result!r}"


@pytest.mark.asyncio
async def test_combine_drains_waiter_of_permanently_lost_member_in_source(tmp_path):
	"""A wait_entry for a permanently_lost member (who stays in source per the
	combine logic) must have its future resolved with the merge sentinel rather
	than being stranded when source ends."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Conv-A (source): one alive member + one permanently_lost member
	conv_a = Conversation(id="conv-a6", title="Source A6")
	m_alice = _make_member("s-alice6", "claude-alice6", alive=True)
	m_carol = _make_member(
		"s-carol6", "claude-carol6",
		alive=False, session_lost_permanently=True,
	)
	conv_a.members_active["s-alice6"] = m_alice
	conv_a.members_active["s-carol6"] = m_carol
	registry.conversations["conv-a6"] = conv_a
	registry.bind_session("s-alice6", "conv-a6")
	# Don't bind Carol's session — she's permanently lost
	registry.set_session_home("s-alice6", "conv-a6")

	# Conv-B (target): the combiner
	conv_b = Conversation(id="conv-b6", title="Target B6")
	m_combiner = _make_member("s-combiner6", "claude-combiner6", alive=True)
	conv_b.members_active["s-combiner6"] = m_combiner
	registry.conversations["conv-b6"] = conv_b
	registry.bind_session("s-combiner6", "conv-b6")
	registry.set_session_home("s-combiner6", "conv-b6")

	# Manually construct a stranded wait_entry for Carol (in practice
	# cli_session_end already cancels these, but we test the defensive drain path).
	loop = asyncio.get_event_loop()
	carol_future = loop.create_future()
	carol_entry = {
		"member": m_carol,
		"future": carol_future,
		"waiting_kind": "msg_and_await",
		"block_position": time.monotonic(),
	}
	conv_a.wait_queue.append(carol_entry)

	# Combine source into target
	result = await handlers.combine_conversations(
		"conv-a6",
		"conv-b6",
		cli_session_id="s-combiner6",
		cwd="C:/Work/B6",
	)
	assert json.loads(result)["status"] == "ok", f"Unexpected result: {result}"

	# Carol stayed in source (permanently_lost path)
	assert "s-carol6" in conv_a.members_active
	# Alice was migrated to target
	assert "s-alice6" in conv_b.members_active

	# source.wait_queue must be empty
	assert len(conv_a.wait_queue) == 0

	# Carol's future must be resolved with the merge sentinel
	assert carol_future.done(), "Carol's future must be resolved, not stranded"
	payload = carol_future.result()
	assert "__CONVERSATION_ENDED__" in payload, \
		f"Carol's drained payload should contain merge sentinel; got: {payload!r}"


