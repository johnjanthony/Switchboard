"""Tests for the five branches of enter_conversation."""

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
		timeout_seconds=5.0,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _make_conv(conv_id: str, title: str = "test", state: str = "active"):
	"""Create a minimal Conversation."""
	c = Conversation(id=conv_id, title=title, state=state)
	return c


def _add_member_to_conv(registry: Registry, conv: Conversation, session_id: str, sender: str, cwd: str = "C:/X"):
	"""Add a ConversationMember to conv and bind the session in the registry."""
	m = ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=0.0,
	)
	conv.members_active[session_id] = m
	registry.bind_session(session_id, conv.id)
	return m


# ---------------------------------------------------------------------------
# Branch 4: open=None, caller unbound → error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_branch_4_no_open_unbound_errors(cfg, logger):
	"""open_conversation_id is None, caller unbound → ERROR."""
	backend = RecordingBackend()
	r = Registry()
	# No open conv, no binding
	assert r.open_conversation_id is None
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.enter_conversation(
		"Claude-X",
		cli_session_id="s-unbound",
		cwd="C:/Work/X",
	)

	assert result.startswith("ERROR:")
	assert "no open conversation" in result


# ---------------------------------------------------------------------------
# Branch 2: caller unbound, open conv exists → joins open conv
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_branch_2_unbound_joins_open(cfg, logger):
	"""open conv exists, caller unbound → caller added as member; queued (blocks)."""
	backend = RecordingBackend()
	r = Registry()
	conv = _make_conv("conv-open")
	# Existing member to keep conv alive
	_add_member_to_conv(r, conv, "s-existing", "Claude-Existing")
	r.conversations["conv-open"] = conv
	r.open_conversation_id = "conv-open"
	handlers = build_tool_handlers(cfg, r, backend, logger)

	# Start enter_conversation — will block waiting for a wake
	task = asyncio.create_task(
		handlers.enter_conversation(
			"Claude-New",
			cli_session_id="s-new",
			cwd="C:/Work/New",
		)
	)

	# Let the handler reach the blocking wait
	await asyncio.sleep(0.05)

	# Verify: caller added to conv and session bound
	assert "s-new" in conv.members_active
	assert r.session_to_conversation_id.get("s-new") == "conv-open"
	# Caller should be in the wait queue
	assert len(conv.wait_queue) == 1
	assert conv.wait_queue[0]["waiting_kind"] == "enter"

	# Cancel task to avoid leaving it dangling
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Branch 1: caller bound to conv X, open=None → queue in X
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_branch_1_bound_no_open_queues_in_current(cfg, logger):
	"""Caller bound to X, open=None → queued in X (Branch 1 / same as Branch 5)."""
	backend = RecordingBackend()
	r = Registry()
	conv = _make_conv("conv-x")
	_add_member_to_conv(r, conv, "s-A", "Claude-A")
	r.conversations["conv-x"] = conv
	# open_conversation_id stays None
	handlers = build_tool_handlers(cfg, r, backend, logger)

	task = asyncio.create_task(
		handlers.enter_conversation(
			"Claude-A",
			cli_session_id="s-A",
			cwd="C:/Work/X",
		)
	)
	await asyncio.sleep(0.05)

	# Caller should still be in conv-x and in its wait queue
	assert r.session_to_conversation_id.get("s-A") == "conv-x"
	assert len(conv.wait_queue) == 1
	assert conv.wait_queue[0]["member"].cli_session_id == "s-A"

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Branch 5: caller bound to X, open == X → queue in X (same as Branch 1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_branch_5_bound_open_same_conv_queues_in_current(cfg, logger):
	"""Caller bound to X, open == X → queued in X (Branch 5)."""
	backend = RecordingBackend()
	r = Registry()
	conv = _make_conv("conv-x")
	_add_member_to_conv(r, conv, "s-A", "Claude-A")
	r.conversations["conv-x"] = conv
	r.open_conversation_id = "conv-x"  # open is the same as current
	handlers = build_tool_handlers(cfg, r, backend, logger)

	task = asyncio.create_task(
		handlers.enter_conversation(
			"Claude-A",
			cli_session_id="s-A",
			cwd="C:/Work/X",
		)
	)
	await asyncio.sleep(0.05)

	assert r.session_to_conversation_id.get("s-A") == "conv-x"
	assert len(conv.wait_queue) == 1

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Branch 3: caller bound to X, open = Y (different) → migrate to Y
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_branch_3_migrates_to_open_conv(cfg, logger):
	"""Caller bound to X, open = Y → migrated from X to Y; queued in Y."""
	backend = RecordingBackend()
	r = Registry()

	conv_x = _make_conv("conv-x")
	_add_member_to_conv(r, conv_x, "s-A", "Claude-A", cwd="C:/Work/X")
	r.conversations["conv-x"] = conv_x

	conv_y = _make_conv("conv-y")
	_add_member_to_conv(r, conv_y, "s-B", "Claude-B", cwd="C:/Work/Y")
	r.conversations["conv-y"] = conv_y
	r.open_conversation_id = "conv-y"

	handlers = build_tool_handlers(cfg, r, backend, logger)

	task = asyncio.create_task(
		handlers.enter_conversation(
			"Claude-A",
			cli_session_id="s-A",
			cwd="C:/Work/X",
		)
	)
	await asyncio.sleep(0.05)

	# Claude-A should now be in conv-y, not conv-x
	assert "s-A" in conv_y.members_active
	assert "s-A" not in conv_x.members_active
	assert r.session_to_conversation_id.get("s-A") == "conv-y"
	# Queued in conv-y
	assert len(conv_y.wait_queue) == 1
	assert conv_y.wait_queue[0]["member"].cli_session_id == "s-A"
	# conv-x should be ended (no alive members remain)
	assert conv_x.state == "ended"

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Wake delivery: enter queue entry wakes when _wake_one_from is called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_wakes_when_message_posted(cfg, logger):
	"""After entering (Branch 1), posting a message via message_and_await wakes the enter waiter."""
	backend = RecordingBackend()
	r = Registry()

	conv = _make_conv("conv-chat")
	_add_member_to_conv(r, conv, "s-A", "Claude-A", cwd="C:/Work/A")
	_add_member_to_conv(r, conv, "s-B", "Claude-B", cwd="C:/Work/B")
	r.conversations["conv-chat"] = conv
	r.open_conversation_id = "conv-chat"

	handlers = build_tool_handlers(cfg, r, backend, logger)

	# A enters (will block, no open message yet)
	task_enter = asyncio.create_task(
		handlers.enter_conversation(
			"Claude-A",
			cli_session_id="s-A",
			cwd="C:/Work/A",
		)
	)
	await asyncio.sleep(0.05)

	# B speaks — this should wake A
	task_speak = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-B",
			message="hello from B",
			cli_session_id="s-B",
			cwd="C:/Work/B",
		)
	)

	result_enter = await asyncio.wait_for(task_enter, timeout=2.0)
	assert "hello from B" in result_enter

	task_speak.cancel()
	try:
		await task_speak
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# Validation: missing cli_session_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_errors_missing_cli_session_id(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.enter_conversation(
		"Claude-X",
		cwd="C:/Work/X",
	)

	assert result.startswith("ERROR: cli_session_id required")
