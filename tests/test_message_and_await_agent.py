"""Tests for the new message_and_await_agent talking-stick FIFO behavior."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.rate_limiter import RateLimiter
from server.registry import SUPERSEDED_SENTINEL, Conversation, ConversationMember, Registry
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


# ---------------------------------------------------------------------------
# Tests: rate-limiter push suppression (REV-109)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limited_agent_msg_still_delivers_but_suppresses_push(cfg, logger):
	"""REV-109: message_and_await_agent shares the per-conversation bucket, but
	degrades by suppressing FCM - a collab ping-pong storm stops buzzing the
	phone without breaking delivery or wake semantics."""
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter=RateLimiter(1))

	task_a = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "first message", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)
	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "second message", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)

	result_a = json.loads(await task_a)
	assert result_a["status"] == "ok"
	assert "second message" in result_a["log"]
	# Both messages were written - delivery is never dropped by the limiter.
	assert len(backend.channel_messages) == 2
	# The second write exceeded the bucket (capacity 1): push suppressed.
	assert backend.push_suppressed == [False, True]

	task_b.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task_b


@pytest.mark.asyncio
async def test_wake_envelope_carries_peer_objects(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task_a = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "hello", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)
	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "hi back", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)

	result = json.loads(await asyncio.wait_for(task_a, timeout=2))
	assert result["status"] == "ok"
	(peer,) = result["peers"]
	assert peer["sender"] == "Claude-B"
	assert peer["state"] == "alive"
	assert set(peer) == {"sender", "state", "waiting", "last_spoke_at"}

	task_b.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task_b


@pytest.mark.asyncio
async def test_agent_msgs_within_limit_push_normally(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter=RateLimiter(30))

	task_a = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "hello", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)
	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "reply", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)

	assert json.loads(await task_a)["status"] == "ok"
	assert backend.push_suppressed == [False, False]

	task_b.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task_b


# ---------------------------------------------------------------------------
# Tests: timeout_seconds clamp (D5)
# ---------------------------------------------------------------------------

def test_clamp_wait_timeout_matrix():
	from server.gateway.handlers import _clamp_wait_timeout
	assert _clamp_wait_timeout(None, 86400.0) == 86400.0
	assert _clamp_wait_timeout(60.0, 86400.0) == 60.0
	assert _clamp_wait_timeout(3.0, 86400.0) == 10.0       # floor
	assert _clamp_wait_timeout(999999.0, 86400.0) == 86400.0  # ceiling
	assert _clamp_wait_timeout(999999.0, 0.3) == 0.3       # ceiling below floor: ceiling wins


@pytest.mark.asyncio
async def test_caller_timeout_is_clamped_to_ceiling(short_timeout_cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(short_timeout_cfg, registry, RecordingBackend(), logger)

	result = json.loads(await asyncio.wait_for(handlers.message_and_await_agent(
		"Claude-A", "anyone there?", timeout_seconds=999999,
		cli_session_id="s-A", cwd="C:/X"), timeout=5))
	assert result["status"] == "timeout"  # 0.3s ceiling applied, not the huge request


@pytest.mark.asyncio
async def test_invalid_timeout_raises_before_any_side_effect(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	conv = registry.conversations["conv-1"]

	with pytest.raises((TypeError, ValueError)):
		await handlers.message_and_await_agent(
			"Claude-A", "hello", timeout_seconds="bogus", cli_session_id="s-A", cwd="C:/X")
	assert len(conv.wait_queue) == 0  # nothing stranded for a peer wake to resolve into the void
	assert len(conv.messages) == 0  # and nothing was appended either


# ---------------------------------------------------------------------------
# Tests: wait supersession (D6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_wait_supersedes_first_from_same_session(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task_1 = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "first question", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)
	task_2 = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "newer question", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)

	first = json.loads(await asyncio.wait_for(task_1, timeout=2))
	assert first["status"] == "superseded"
	conv = registry.conversations["conv-1"]
	assert len(conv.wait_queue) == 1  # only the newer wait remains

	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "answering", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)
	second = json.loads(await asyncio.wait_for(task_2, timeout=2))
	assert second["status"] == "ok"
	assert "Claude-B: answering" in second["log"]

	task_b.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task_b


# ---------------------------------------------------------------------------
# Tests: post-wake conversation re-resolution (combine while parked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wake_after_combine_reports_target_conversation_and_peers(cfg, logger):
	"""A wait parked in the source room and migrated by a combine must wake
	reporting the TARGET conversation and its live peers. The pre-park capture
	named the now-Ended source with peers=[], which the last-agent-standing rule
	tells the agent to act on by reporting to John and stopping."""
	backend = RecordingBackend()
	registry = Registry()
	source = Conversation(id="conv-src", title="source")
	source.members_active["s-P"] = ConversationMember(
		cli_session_id="s-P", sender="Claude-P", cwd="C:/P", surface="windows", joined_at=0.0,
	)
	registry.conversations["conv-src"] = source
	registry.bind_session("s-P", "conv-src")
	target = Conversation(id="conv-tgt", title="target")
	target.members_active["s-T"] = ConversationMember(
		cli_session_id="s-T", sender="Claude-T", cwd="C:/T", surface="windows", joined_at=0.0,
	)
	registry.conversations["conv-tgt"] = target
	registry.bind_session("s-T", "conv-tgt")
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	parked = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-P", "parking in source", cli_session_id="s-P", cwd="C:/P"))
	await asyncio.sleep(0.05)
	assert len(source.wait_queue) == 1

	combined = await handlers.combine_conversations(
		"conv-src", "conv-tgt", cli_session_id="s-T", cwd="C:/T")
	assert json.loads(combined)["status"] == "ok"

	result = json.loads(await asyncio.wait_for(parked, timeout=2))
	assert result["status"] == "ok"
	assert result["conversation_id"] == "conv-tgt"
	assert [p["sender"] for p in result["peers"]] == ["Claude-T"]


@pytest.mark.asyncio
async def test_supersede_preserves_cursor_for_undelivered_history(cfg, logger):
	"""SUPERSEDED_SENTINEL carries no log, so superseding a parked wait must not
	advance the cursor past history that wait never delivered. The reachable worst
	case is the combine-migrated state: cursor 0 plus a tail queue position, where
	burning the debt loses the whole target history including the combine intro."""
	backend = RecordingBackend()
	registry, conv_id = _make_registry_with_two_alive_members()
	conv = registry.conversations[conv_id]
	conv.messages.append({
		"seq": 0, "sender": "<system>", "type": "system",
		"text": "Claude-A joined via combine.", "timestamp": "t0",
	})
	conv.messages.append({
		"seq": 1, "sender": "Claude-B", "type": "agent_msg",
		"text": "target history from B", "timestamp": "t1",
	})
	member_a = conv.members_active["s-A"]
	member_a.last_seen_seq = 0
	migrated_future = asyncio.get_event_loop().create_future()
	conv.wait_queue.append({
		"member": member_a, "future": migrated_future,
		"waiting_kind": "msg_and_await", "block_position": time.monotonic(),
	})
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	reissued = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "re-issuing my wait", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)

	assert migrated_future.result() == SUPERSEDED_SENTINEL
	assert member_a.last_seen_seq == 0  # debt transferred to the new wait, not burned

	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "answering", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)
	woken = json.loads(await asyncio.wait_for(reissued, timeout=2))
	assert woken["status"] == "ok"
	assert "joined via combine" in woken["log"]
	assert "target history from B" in woken["log"]
	assert "Claude-B: answering" in woken["log"]

	task_b.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task_b
