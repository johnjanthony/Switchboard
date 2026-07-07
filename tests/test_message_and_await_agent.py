"""Tests for the new message_and_await_agent talking-stick FIFO behavior."""

from __future__ import annotations

import asyncio
import json

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
def short_timeout_cfg(tmp_path):
	"""For tests that exercise the lobby-hold timeout — opener waits this long
	for the next peer before getting __TIMEOUT__. Keep small to run fast."""
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0.3,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _make_registry_with_two_alive_members():
	"""Helper: returns (registry, conv_id) with a conversation containing two alive members."""
	r = Registry()
	conv = Conversation(id="conv-1", title="test")
	a = ConversationMember(
		cli_session_id="s-A",
		sender="Claude-A",
		cwd="C:/X",
		surface="windows",
		joined_at=0.0,
	)
	b = ConversationMember(
		cli_session_id="s-B",
		sender="Claude-B",
		cwd="C:/Y",
		surface="windows",
		joined_at=0.0,
	)
	conv.members_active["s-A"] = a
	conv.members_active["s-B"] = b
	r.conversations["conv-1"] = conv
	r.bind_session("s-A", "conv-1")
	r.bind_session("s-B", "conv-1")
	return r, "conv-1"


def _make_registry_with_one_alive_member():
	"""Helper: single-member conversation."""
	r = Registry()
	conv = Conversation(id="conv-solo", title="solo test")
	m = ConversationMember(
		cli_session_id="s-solo",
		sender="Claude-Solo",
		cwd="C:/Z",
		surface="windows",
		joined_at=0.0,
	)
	conv.members_active["s-solo"] = m
	r.conversations["conv-solo"] = conv
	r.bind_session("s-solo", "conv-solo")
	return r, "conv-solo"


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_and_await_rejects_missing_message(cfg, logger):
	"""Empty or None message is rejected immediately."""
	backend = RecordingBackend()
	r, _ = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-A",
		message="",
		cli_session_id="s-A",
		cwd="C:/X",
	)

	assert result.startswith("ERROR: message is required")


@pytest.mark.asyncio
async def test_message_and_await_rejects_none_message(cfg, logger):
	"""None message is also rejected."""
	backend = RecordingBackend()
	r, _ = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-A",
		message=None,
		cli_session_id="s-A",
		cwd="C:/X",
	)

	assert result.startswith("ERROR: message is required")


@pytest.mark.asyncio
async def test_message_and_await_rejects_unbound_session(cfg, logger):
	"""Session not bound to any conversation returns the correct error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-X",
		message="hello",
		cli_session_id="s-unbound",
		cwd="C:/X",
	)

	assert "not in any conversation" in result


@pytest.mark.asyncio
async def test_message_and_await_rejects_missing_cli_session_id(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-X",
		message="hello",
		cwd="C:/X",
	)

	assert result.startswith("ERROR: cli_session_id required")


# ---------------------------------------------------------------------------
# Tests: sole alive member parks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sole_alive_speaker_parks_and_is_woken_by_joiner(cfg, logger):
	"""A solo speaker appends its message and PARKS in the normal wait_queue
	instead of auto-leaving or holding a lobby. The conversation stays active
	and the caller stays a member. A later joiner's speak wakes the parked
	future via the existing FIFO _wake_one_from, and the joiner then parks
	in turn."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	solo_task = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-Solo",
		message="anyone here?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	))
	for _ in range(5):
		await asyncio.sleep(0)

	conv = r.conversations[conv_id]
	assert len(conv.wait_queue) == 1
	assert conv.state == "active"
	assert "s-solo" in conv.members_active
	assert not solo_task.done()

	joiner_task = asyncio.create_task(handlers.join_conversation(
		"Joiner",
		ref=conv_id,
		cli_session_id="s-joiner",
		cwd="/home/j",
	))
	for _ in range(5):
		await asyncio.sleep(0)

	speak_task = asyncio.create_task(handlers.message_and_await_agent(
		"Joiner",
		message="I'm here",
		cli_session_id="s-joiner",
		cwd="/home/j",
	))

	result = await asyncio.wait_for(solo_task, timeout=2.0)
	data = json.loads(result)
	assert data["status"] == "ok"
	assert "I'm here" in data["log"]
	assert len(conv.wait_queue) == 1  # joiner now parked in turn

	joiner_task.cancel()
	speak_task.cancel()
	for t in (joiner_task, speak_task):
		try:
			await t
		except asyncio.CancelledError:
			pass


@pytest.mark.asyncio
async def test_sole_alive_parker_times_out(short_timeout_cfg, logger):
	"""A solo speaker who parks and gets no reply within the timeout gets the
	ordinary timeout envelope; the conversation and membership are untouched
	and the wait entry is removed from the queue."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	handlers = build_tool_handlers(short_timeout_cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-Solo",
		message="anyone?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	)

	assert json.loads(result) == {"status": "timeout"}
	conv = r.conversations[conv_id]
	assert conv.state == "active"
	assert "s-solo" in conv.members_active
	assert len(conv.wait_queue) == 0


@pytest.mark.asyncio
async def test_parked_solo_woken_by_peer_leave(cfg, logger):
	"""A parked solo speaker is woken when a joining peer immediately leaves
	with a parting message — leave_conversation's own _wake_one_from call
	resolves the parked future with the parting text."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	solo_task = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-Solo",
		message="anyone here?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	))
	for _ in range(5):
		await asyncio.sleep(0)

	join_result = await handlers.join_conversation(
		"Joiner",
		ref=conv_id,
		cli_session_id="s-joiner",
		cwd="/home/j",
	)
	assert join_result

	leave_result = await handlers.leave_conversation(
		"Joiner",
		"gotta go",
		cli_session_id="s-joiner",
		cwd="/home/j",
	)
	assert leave_result

	result = await asyncio.wait_for(solo_task, timeout=2.0)
	assert "gotta go" in result


# ---------------------------------------------------------------------------
# Tests: cancel resets session-registry state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_wait_resets_session_state(cfg, logger):
	"""Cancelling a parked message_and_await_agent call (the Esc-in-CLI path)
	resets the caller's session-registry state so the roster does not show a
	stale awaiting_* chip forever."""
	from server.session_registry import SessionRegistry

	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	session_registry = SessionRegistry()
	session_registry.record_session_start("s-A", cwd="C:/X")
	session_registry.upsert_from_hook("s-A", state="awaiting_agent", event="PreToolUse")
	handlers = build_tool_handlers(cfg, r, backend, logger, session_registry=session_registry)

	task_a = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A",
		message="parking",
		cli_session_id="s-A",
		cwd="C:/X",
	))
	for _ in range(5):
		await asyncio.sleep(0)

	conv = r.conversations[conv_id]
	assert len(conv.wait_queue) == 1

	task_a.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task_a

	rec = session_registry.get("s-A")
	assert rec.state == "active"
	assert rec.state_detail == "wait-cancelled"
	assert len(conv.wait_queue) == 0


# ---------------------------------------------------------------------------
# Tests: two-agent ping-pong
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_agent_ping_pong_basic(cfg, logger):
	"""A speaks (blocks). B speaks (wakes A with B's message, B blocks). Verify A wakes with B's message."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	# A speaks first — blocks waiting for a reply
	task_a = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-A",
			message="hello from A",
			cli_session_id="s-A",
			cwd="C:/X",
		)
	)
	# Let A enqueue itself
	await asyncio.sleep(0.05)

	# B speaks — should wake A and then block itself
	task_b = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-B",
			message="hi back from B",
			cli_session_id="s-B",
			cwd="C:/Y",
		)
	)

	# A should wake with B's message
	result_a = await asyncio.wait_for(task_a, timeout=2.0)
	assert "hi back from B" in result_a
	# A's own message should NOT appear in the payload
	assert "hello from A" not in result_a

	# Clean up B's task (it's blocked waiting)
	task_b.cancel()
	try:
		await task_b
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_wake_payload_excludes_callers_own_messages(cfg, logger):
	"""After A and B have exchanged messages, A wakes and sees only B's messages (not its own)."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	# A speaks
	task_a = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-A",
			message="message one from A",
			cli_session_id="s-A",
			cwd="C:/X",
		)
	)
	await asyncio.sleep(0.05)

	# B speaks — wakes A
	task_b = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-B",
			message="message one from B",
			cli_session_id="s-B",
			cwd="C:/Y",
		)
	)

	result_a = await asyncio.wait_for(task_a, timeout=2.0)

	# A's wake payload should have B's message, not A's own
	assert "message one from B" in result_a
	assert "message one from A" not in result_a

	task_b.cancel()
	try:
		await task_b
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_speak_with_no_waiters_just_appends_log(cfg, logger):
	"""If conv has multiple alive members but none are waiting, a speak appends
	to the log without waking anyone. Caller still enqueues and blocks."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	conv = r.conversations[conv_id]
	# No waiters in queue yet
	assert len(conv.wait_queue) == 0

	# A speaks — nobody to wake, so A just enqueues
	task_a = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-A",
			message="no one waiting yet",
			cli_session_id="s-A",
			cwd="C:/X",
		)
	)
	await asyncio.sleep(0.05)

	# A should now be in the wait queue
	assert len(conv.wait_queue) == 1
	assert conv.wait_queue[0]["member"].sender == "Claude-A"
	# Message should be appended to the log
	assert any(m.get("text") == "no one waiting yet" for m in conv.messages)

	task_a.cancel()
	try:
		await task_a
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_timeout_cleans_up_wait_entry(cfg, logger, tmp_path):
	"""On timeout, the wait entry is removed from the queue and TIMEOUT_SENTINEL is returned."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	# Use a very short timeout
	short_cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0.1,
		log_path=str(tmp_path / "log.jsonl"),
	)
	short_logger = JsonlLogger(short_cfg.log_path)
	handlers = build_tool_handlers(short_cfg, r, backend, short_logger)

	conv = r.conversations[conv_id]

	# A speaks — will time out waiting for B
	result = await handlers.message_and_await_agent(
		"Claude-A",
		message="will timeout",
		cli_session_id="s-A",
		cwd="C:/X",
	)

	assert json.loads(result) == {"status": "timeout"}
	# Queue should be cleaned up
	assert len(conv.wait_queue) == 0


@pytest.mark.asyncio
async def test_last_seen_seq_updated_after_wake(cfg, logger):
	"""After A wakes, A's last_seen_seq should point to the end of the message log."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	conv = r.conversations[conv_id]
	member_a = conv.members_active["s-A"]
	initial_seq = member_a.last_seen_seq

	task_a = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-A",
			message="seq test A",
			cli_session_id="s-A",
			cwd="C:/X",
		)
	)
	await asyncio.sleep(0.05)

	# last_seen_seq updated after enqueueing (A has seen its own speak event)
	seq_after_enqueue = member_a.last_seen_seq
	assert seq_after_enqueue > initial_seq

	# B speaks — wakes A
	task_b = asyncio.create_task(
		handlers.message_and_await_agent(
			"Claude-B",
			message="seq test B",
			cli_session_id="s-B",
			cwd="C:/Y",
		)
	)

	await asyncio.wait_for(task_a, timeout=2.0)

	# After wake, A's last_seen_seq should point to end of messages
	assert member_a.last_seen_seq == len(conv.messages)

	task_b.cancel()
	try:
		await task_b
	except asyncio.CancelledError:
		pass
