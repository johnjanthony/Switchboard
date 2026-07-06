"""End-to-end test: open_conversation + enter_conversation flow."""

from __future__ import annotations

import asyncio

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "log.jsonl"),
	)


# ---------------------------------------------------------------------------
# Test 1: A creates conv via ask_human, opens it, B enters and wakes with message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_open_enter_basic(tmp_path):
	"""A creates conv via ask_human (auto-create), opens it, B enters (blocks),
	A calls message_and_await_agent (wakes B), B receives A's message."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Step 1: A calls notify_human first → auto-creates conv-1 with A as sole member
	await handlers.notify_human(
		"starting investigation",
		sender="claude-a",
		cli_session_id="s-A",
		cwd="C:/Work/X",
	)

	# Verify A is now bound to a conversation
	conv_id = registry.session_to_conversation_id.get("s-A")
	assert conv_id is not None, "A should be bound after notify_human"
	conv = registry.conversations[conv_id]
	assert "s-A" in conv.members_active

	# Step 2: A calls open_conversation → conv becomes the open conversation
	result = await handlers.open_conversation(
		"claude-a",
		title="bug investigation",
		cli_session_id="s-A",
		cwd="C:/Work/X",
	)
	assert result == f"ok. open_conversation = {conv_id}"
	assert registry.open_conversation_id == conv_id

	# Step 3: B (unbound) calls enter_conversation → joins conv, blocks waiting for intro
	task_b_enter = asyncio.create_task(
		handlers.enter_conversation(
			"claude-b",
			cli_session_id="s-B",
			cwd="C:/Work/Y",
		)
	)
	# Give enter_conversation time to reach the blocking wait
	await asyncio.sleep(0.05)

	# Verify B was added to the conversation
	assert "s-B" in conv.members_active
	assert registry.session_to_conversation_id.get("s-B") == conv_id
	assert len(conv.wait_queue) == 1
	assert conv.wait_queue[0]["waiting_kind"] == "enter"

	# Step 4: A calls message_and_await_agent → writes a speak event, wakes B
	# A will also block (waiting for B to speak back), so we run it as a task
	task_a_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-a",
			message="welcome b",
			cli_session_id="s-A",
			cwd="C:/Work/X",
		)
	)

	# Step 5: B should wake with A's speak event included in the full history
	result_b = await asyncio.wait_for(task_b_enter, timeout=2.0)
	assert "welcome b" in result_b, f"B's enter payload should contain A's speak; got: {result_b!r}"

	# Clean up A's blocked task
	task_a_speak.cancel()
	try:
		await task_a_speak
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Test 2: open_conversation replaces prior open pointer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_open_replaces_prior_open(tmp_path):
	"""Two convs; one becomes open; second agent calls open_conversation;
	registry.open_conversation_id flips to the new conv."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Create conv-1 with Agent A
	conv1 = Conversation(id="conv-1", title="first")
	m1 = ConversationMember(cli_session_id="s-A", sender="claude-a", cwd="C:/X", surface="windows", joined_at=0.0)
	conv1.members_active["s-A"] = m1
	registry.conversations["conv-1"] = conv1
	registry.bind_session("s-A", "conv-1")
	registry.open_conversation_id = "conv-1"  # conv-1 is initially open

	# Create conv-2 with Agent B
	conv2 = Conversation(id="conv-2", title="second")
	m2 = ConversationMember(cli_session_id="s-B", sender="claude-b", cwd="C:/Y", surface="windows", joined_at=0.0)
	conv2.members_active["s-B"] = m2
	registry.conversations["conv-2"] = conv2
	registry.bind_session("s-B", "conv-2")

	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# B opens its conversation → replaces conv-1's open pointer
	result = await handlers.open_conversation(
		"claude-b",
		cli_session_id="s-B",
		cwd="C:/Y",
	)

	assert result == "ok. open_conversation = conv-2"
	assert registry.open_conversation_id == "conv-2"
	# conv-1 still exists and is active (just not the open conv)
	assert registry.conversations["conv-1"].state == "active"


# ---------------------------------------------------------------------------
# Test 3: enter_conversation without open conv → error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_enter_without_open_errors(tmp_path):
	"""B (unbound session) calls enter_conversation when openConversationId is None → ERROR."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	assert registry.open_conversation_id is None

	result = await handlers.enter_conversation(
		"claude-b",
		cli_session_id="s-B",
		cwd="C:/Work/Y",
	)

	assert result.startswith("ERROR:")
	assert "no open conversation" in result


# ---------------------------------------------------------------------------
# Test 4: full lifecycle — open, enter, speak, leave
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_open_enter_speak_full_lifecycle(tmp_path):
	"""Full lifecycle: A creates conv, opens it, B enters, A welcomes B,
	B speaks back (wakes A), A reads B's message correctly."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# A creates conv via notify_human
	await handlers.notify_human(
		"init",
		sender="claude-a",
		cli_session_id="s-A",
		cwd="C:/Work/X",
	)
	conv_id = registry.session_to_conversation_id["s-A"]

	# A opens it
	await handlers.open_conversation(
		"claude-a",
		title="full lifecycle test",
		cli_session_id="s-A",
		cwd="C:/Work/X",
	)
	assert registry.open_conversation_id == conv_id

	# B enters — blocks
	task_b_enter = asyncio.create_task(
		handlers.enter_conversation(
			"claude-b",
			cli_session_id="s-B",
			cwd="C:/Work/Y",
		)
	)
	await asyncio.sleep(0.05)

	# A speaks — wakes B, A then blocks
	task_a_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-a",
			message="hello claude-b, glad you joined",
			cli_session_id="s-A",
			cwd="C:/Work/X",
		)
	)

	# B wakes — receives A's welcome
	b_intro = await asyncio.wait_for(task_b_enter, timeout=2.0)
	assert "hello claude-b, glad you joined" in b_intro

	# B speaks back — wakes A
	task_b_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"claude-b",
			message="thanks for having me",
			cli_session_id="s-B",
			cwd="C:/Work/Y",
		)
	)

	# A wakes with B's reply
	a_result = await asyncio.wait_for(task_a_speak, timeout=2.0)
	assert "thanks for having me" in a_result
	# A should NOT see its own message in the wake payload
	assert "hello claude-b" not in a_result

	# Clean up B's blocked task
	task_b_speak.cancel()
	try:
		await task_b_speak
	except asyncio.CancelledError:
		pass
