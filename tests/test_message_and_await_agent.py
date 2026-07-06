"""Tests for the new message_and_await_agent talking-stick FIFO behavior."""

from __future__ import annotations

import asyncio

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
# Tests: sole alive member
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_and_await_sole_alive_member_returns_empty_sentinel(cfg, logger):
	"""Single-member conversation — calling message_and_await returns __CONVERSATION_EMPTY__ immediately.
	Per Fix Pack 1 / Bug #6 the sentinel return is now matched by actual member
	removal: the caller is dropped from members_active, the conv transitions to
	"ended" (no remaining members), and session-fallback rebinds/unbinds the
	caller so the agent's "I was removed" belief matches reality."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-Solo",
		message="hello?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	)

	assert result == "__CONVERSATION_EMPTY__"
	# Caller removed from members_active and recorded in history.
	conv = r.conversations[conv_id]
	assert "s-solo" not in conv.members_active
	assert any(m.cli_session_id == "s-solo" for m in conv.members_history)
	# No remaining members → conversation Ended.
	assert conv.state == "ended"
	assert conv.ended_at is not None
	# Session-fallback applied: global_away_mode default False → unbind.
	assert "s-solo" not in r.session_to_conversation_id


@pytest.mark.asyncio
async def test_sole_alive_in_open_marker_conv_blocks_until_peer_joins(cfg, logger):
	"""When the sole-alive caller is in the conv that holds the open marker,
	message_and_await_agent does NOT auto-leave. It blocks via the same
	open_peer_future mechanism as the mint-path bootstrap, so the lobby stays
	alive across peers coming and going.

	A peer-join concurrently unblocks the lobby-holder with a 'Peer X joined'
	payload, and the conv is still active afterwards."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	# This conv is the open marker — the lobby case.
	r.open_conversation_id = conv_id
	handlers = build_tool_handlers(cfg, r, backend, logger)

	lobby_task = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-Solo",
		message="anyone here?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	))
	await asyncio.sleep(0.1)
	# Lobby-holder should be blocked, not auto-leaving
	assert not lobby_task.done(), "sole-alive in open-marker conv should block, not auto-leave"
	assert "s-solo" in r.conversations[conv_id].members_active

	# A peer joins to unblock the lobby
	peer_task = asyncio.create_task(handlers.enter_conversation(
		"Joiner",
		cli_session_id="s-joiner",
		cwd="/home/j",
	))
	result = await asyncio.wait_for(lobby_task, timeout=2.0)

	assert "Joiner" in result, f"Expected joiner sender in wake payload, got: {result!r}"
	# Conv is still Active; lobby-holder remains a member; open marker still set
	conv = r.conversations[conv_id]
	assert conv.state == "active"
	assert "s-solo" in conv.members_active
	assert r.open_conversation_id == conv_id

	peer_task.cancel()
	try:
		await peer_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_sole_alive_in_open_marker_conv_times_out_without_ending(short_timeout_cfg, logger):
	"""Lobby-hold timeout: returns __TIMEOUT__ but does NOT force-end the conv
	or clear the open marker. The caller stays a member and can poll again or
	explicitly leave. Distinguishes the mid-conversation lobby case from the
	bootstrap mint case (which DOES force-end on timeout to clean up an
	orphan room that no one ever joined)."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	r.open_conversation_id = conv_id
	handlers = build_tool_handlers(short_timeout_cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-Solo",
		message="anyone?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	)

	assert result == "__TIMEOUT__"
	conv = r.conversations[conv_id]
	assert conv.state == "active", "lobby timeout should NOT end the conv"
	assert "s-solo" in conv.members_active, "caller should remain a member"
	assert r.open_conversation_id == conv_id, "open marker should still be set"


@pytest.mark.asyncio
async def test_sole_alive_in_non_open_conv_still_auto_leaves(cfg, logger):
	"""Belt-and-suspenders: confirm we ONLY changed behavior for the open-marker
	case. A sole-alive caller in a conversation that is NOT the open marker
	still hits the original auto-leave path."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	# Deliberately NOT setting r.open_conversation_id — this is an ad-hoc conv.
	assert r.open_conversation_id is None
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-Solo",
		message="hello?",
		cli_session_id="s-solo",
		cwd="C:/Z",
	)

	assert result == "__CONVERSATION_EMPTY__"
	conv = r.conversations[conv_id]
	assert "s-solo" not in conv.members_active
	assert conv.state == "ended"


@pytest.mark.asyncio
async def test_sole_member_empty_sentinel_includes_partings(cfg, logger):
	"""Sole alive member sees parting messages in the __CONVERSATION_EMPTY__ payload."""
	backend = RecordingBackend()
	r = Registry()
	conv = Conversation(id="conv-p", title="parting test")
	m = ConversationMember(
		cli_session_id="s-last",
		sender="Claude-Last",
		cwd="C:/P",
		surface="windows",
		joined_at=0.0,
	)
	conv.members_active["s-last"] = m
	# Inject a parting message that was added before the caller's last_seen_seq is updated
	conv.messages.append({
		"seq": 0,
		"sender": "Claude-Other",
		"type": "parting",
		"text": "goodbye world",
		"timestamp": "2026-01-01T00:00:00+00:00",
		"title": None,
	})
	# last_seen_seq = 0, so the parting is "since last_seen_seq"
	m.last_seen_seq = 0
	r.conversations["conv-p"] = conv
	r.bind_session("s-last", "conv-p")
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-Last",
		message="still here?",
		cli_session_id="s-last",
		cwd="C:/P",
	)

	assert "__CONVERSATION_EMPTY__" in result
	assert "goodbye world" in result


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

	assert result == "__TIMEOUT__"
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
