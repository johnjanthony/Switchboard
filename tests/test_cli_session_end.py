"""Tests for handle_session_end, which marks members dormant on SessionEnd."""

import asyncio

import pytest

from server.cli_session_end import handle_session_end
from server.registry import Conversation, ConversationMember, Registry


def _fixed_now():
	return "2026-05-20T00:00:00Z"


class _FakeBackend:
	"""Minimal recording backend for the REV-001 session-end pending tests."""

	def __init__(self):
		self.cancelled = []
		self.member_writes = []
		self.message_writes = []

	async def write_conversation_member(self, conv_id, member):
		self.member_writes.append((conv_id, member))

	async def write_conversation_message(self, conv_id, message):
		self.message_writes.append((conv_id, message))

	async def mark_question_cancelled(self, conversation_id, request_id):
		self.cancelled.append((conversation_id, request_id))


def _setup_conv_with_member():
	"""A Registry with one Active conversation holding one alive member bound
	to session 'sess-A', plus a fresh _FakeBackend. Returns
	(registry, backend, conversation_id, session_id)."""
	registry = Registry()
	backend = _FakeBackend()
	conv = Conversation(id="conv-1", title="REV-001 test")
	member = ConversationMember(
		cli_session_id="sess-A", sender="Claude", cwd="C:/X",
		surface="windows", joined_at=0.0,
	)
	conv.members_active["sess-A"] = member
	registry.conversations["conv-1"] = conv
	registry.bind_session("sess-A", "conv-1")
	return registry, backend, "conv-1", "sess-A"


@pytest.mark.asyncio
async def test_dormant_mark():
	"""SessionEnd with reason='logout' marks member dormant but not permanently lost."""
	from tests.conftest import make_active_conversation
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	await handle_session_end(
		registry=registry,
		session_id="s-1",
		reason="logout",
		now=_fixed_now,
	)

	member = conv.members_active["s-1"]
	assert member.alive is False
	assert member.session_ended_at == "2026-05-20T00:00:00Z"
	assert member.session_end_reason == "logout"
	assert member.session_lost_permanently is False
	# binding cleared
	assert "s-1" not in registry.session_to_conversation_id


@pytest.mark.asyncio
async def test_permanently_lost_on_clear_or_compact():
	"""SessionEnd with reason='compact' (or 'clear') sets session_lost_permanently."""
	from tests.conftest import make_active_conversation
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	await handle_session_end(registry=registry, session_id="s-1", reason="compact", now=_fixed_now)
	assert conv.members_active["s-1"].session_lost_permanently is True


@pytest.mark.asyncio
async def test_unknown_session_noop():
	"""No binding exists for the session_id; handler returns cleanly."""
	registry = Registry()
	await handle_session_end(registry=registry, session_id="s-unknown", reason="logout", now=_fixed_now)
	# No assertions — just that it didn't raise.


@pytest.mark.asyncio
async def test_session_end_wakes_blocked_peer():
	"""Per Fix Pack 1 / Bug #4: when a session ends while a peer is blocked in
	message_and_await_agent on the same conversation, the peer's future must
	be resolved with the dormancy text — not left to wait 24h."""
	import asyncio
	from server.registry import Conversation, ConversationMember
	registry = Registry()
	conv = Conversation(id="conv-1", title="wake test")
	a = ConversationMember(
		cli_session_id="s-A", sender="Claude-A", cwd="C:/X",
		surface="windows", joined_at=0.0,
	)
	b = ConversationMember(
		cli_session_id="s-B", sender="Claude-B", cwd="C:/Y",
		surface="windows", joined_at=0.0,
	)
	conv.members_active["s-A"] = a
	conv.members_active["s-B"] = b
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-A", "conv-1")
	registry.bind_session("s-B", "conv-1")

	# Peer A is blocked in message_and_await_agent — simulate by appending a
	# wait_queue entry with an unresolved future.
	loop = asyncio.get_event_loop()
	a_future: asyncio.Future = loop.create_future()
	conv.wait_queue.append({
		"member": a,
		"future": a_future,
		"waiting_kind": "msg_and_await",
		"block_position": 0.0,
	})

	# Session B ends.
	await handle_session_end(
		registry=registry, session_id="s-B", reason="logout", now=_fixed_now,
	)

	# Peer A's future must be resolved (not still pending) and the wait_queue cleared.
	assert a_future.done()
	assert "dormant" in a_future.result()
	assert len(conv.wait_queue) == 0


@pytest.mark.asyncio
async def test_session_end_cancels_pending_when_sender_was_disambiguated():
	"""M2: ask_human keys a pending by the raw agent-supplied sender, but a
	member can be stored under a DISAMBIGUATED sender (e.g. 'Claude 2' on a
	same-name collision). The session-end cleanup must cancel the departing
	member's pending by routing identity (cli_session_id), not by comparing the
	disambiguated member.sender against the raw pending key — otherwise the
	pending is orphaned and blocks until the 24h timeout."""
	import asyncio
	from server.registry import Conversation, ConversationMember
	registry = Registry()
	conv = Conversation(id="conv-1", title="disambig test")
	# Member stored under a DISAMBIGUATED sender, bound to session s-1.
	m = ConversationMember(
		cli_session_id="s-1", sender="Claude 2", cwd="C:/X",
		surface="windows", joined_at=0.0,
	)
	conv.members_active["s-1"] = m
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	# ask_human keyed the pending by the RAW sender 'Claude' (what the agent
	# passed), carrying the owning session's id for routing-identity matching.
	future, _ = registry.add(
		conversation_id="conv-1", sender="Claude", request_id="req-1",
		cli_session_id="s-1", return_superseded=True,
	)
	assert registry.pending_count == 1

	await handle_session_end(
		registry=registry, session_id="s-1", reason="logout", now=_fixed_now,
	)

	assert registry.pending_count == 0, "departing member's pending must be cancelled by session identity"
	assert future.cancelled() or (future.done() and isinstance(future.exception(), asyncio.CancelledError))


@pytest.mark.asyncio
async def test_session_end_cancels_dormant_members_pending_ask_human():
	"""Per Fix Pack 1 / Bug #4: when a session ends, any ask_human pending
	request owned by that session's member is cancelled (its future will never
	be resolved by an answer that can't arrive)."""
	import asyncio
	from tests.conftest import make_active_conversation
	registry = Registry()
	conv = make_active_conversation(
		conversation_id="conv-1", member_session_id="s-1", sender="Claude",
	)
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	# Simulate an in-flight ask_human pending for Claude on conv-1.
	future, _ = registry.add(
		conversation_id="conv-1", cli_session_id="s-1", sender="Claude", request_id="req-1",
		return_superseded=True,
	)
	assert registry.pending_count == 1

	await handle_session_end(
		registry=registry, session_id="s-1", reason="logout", now=_fixed_now,
	)

	# Pending entry removed.
	assert registry.pending_count == 0
	# Future was cancelled.
	assert future.cancelled() or (future.done() and isinstance(future.exception(), asyncio.CancelledError))


def test_session_end_logout_keeps_parked_pending():
	# T-001 mainline: restart parks the question, /exit is the documented
	# recovery. A resumable end must NOT kill the parked question.
	async def run():
		registry, backend, conv_id, sid = _setup_conv_with_member()
		registry.add_parked(conv_id, sid, "Claude", "req-park", question="Deploy?")
		await handle_session_end(registry, sid, "logout", now=lambda: "2026-07-08T00:00:00+00:00", backend=backend)
		assert registry.pending_count == 1
		assert registry.find_by_request_id(conv_id, "req-park") is not None
		assert backend.cancelled == []
	asyncio.run(run())


def test_session_end_clear_terminates_parked_pending():
	# Permanently-lost session: nothing can ever deliver the answer. The
	# record terminates properly - Firebase cancel + replay memory - so a
	# later answer is a benign replay, not a false "reply withdrawn" (REV-001).
	async def run():
		registry, backend, conv_id, sid = _setup_conv_with_member()
		registry.add_parked(conv_id, sid, "Claude", "req-park", question="Deploy?")
		await handle_session_end(registry, sid, "clear", now=lambda: "2026-07-08T00:00:00+00:00", backend=backend)
		assert registry.pending_count == 0
		assert backend.cancelled == [(conv_id, "req-park")]
		assert registry.was_recently_resolved(conv_id, "req-park")
	asyncio.run(run())


def test_session_end_cancels_live_pending_without_direct_firebase_write():
	# Live future: cancelling frees the asker coroutine, whose CancelledError
	# arm owns the Firebase cancel - handle_session_end must not double-write.
	async def run():
		registry, backend, conv_id, sid = _setup_conv_with_member()
		fut = registry.add(conv_id, sid, "Claude", "req-live")
		await handle_session_end(registry, sid, "logout", now=lambda: "2026-07-08T00:00:00+00:00", backend=backend)
		assert fut.cancelled()
		assert registry.pending_count == 0
		assert backend.cancelled == []
	asyncio.run(run())


def test_session_end_leaves_other_sessions_pendings_alone():
	async def run():
		registry, backend, conv_id, sid = _setup_conv_with_member()
		registry.add_parked(conv_id, "sess-other", "Peer", "req-other")
		await handle_session_end(registry, sid, "clear", now=lambda: "2026-07-08T00:00:00+00:00", backend=backend)
		assert registry.find_by_request_id(conv_id, "req-other") is not None
	asyncio.run(run())
