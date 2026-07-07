"""Tests for the pending-request registry."""

import asyncio
import json
from datetime import datetime

import pytest

from server.registry import PendingRequest, Registry


class TestPendingByKey:
	def test_add_keys_by_session_and_resolve_by_request_id(self):
		async def run():
			r = Registry()
			fut = r.add("conv-1", "sess-A", "Claude", "req-1")
			assert isinstance(fut, asyncio.Future)
			assert r.find_by_request_id("conv-1", "req-1") is not None
			assert r.resolve("conv-1", "req-1", "answer") == "req-1"
			assert fut.result() == "answer"
			assert r.find_by_request_id("conv-1", "req-1") is None
		asyncio.run(run())

	def test_same_sender_different_sessions_do_not_collide(self):
		async def run():
			r = Registry()
			fut_a = r.add("conv-1", "sess-A", "Claude", "req-A")
			fut_b = r.add("conv-1", "sess-B", "Claude", "req-B")
			assert not fut_a.cancelled()
			assert r.resolve("conv-1", "req-A", "for A") == "req-A"
			assert fut_a.result() == "for A"
			assert not fut_b.done()
		asyncio.run(run())

	def test_same_session_supersedes_prior_pending(self):
		async def run():
			r = Registry()
			fut1, prior = r.add("conv-1", "sess-A", "Claude", "req-1", return_superseded=True)
			assert prior is None
			fut2, prior = r.add("conv-1", "sess-A", "Claude", "req-2", return_superseded=True)
			assert prior == "req-1"
			assert fut1.cancelled()
		asyncio.run(run())

	def test_resolve_unknown_request_id_is_noop(self):
		async def run():
			r = Registry()
			r.add("conv-1", "sess-A", "Claude", "req-1")
			assert r.resolve("conv-1", "req-STALE", "text") is None
			assert r.find_by_request_id("conv-1", "req-1") is not None
		asyncio.run(run())

	def test_remove_is_keyed_by_session_with_request_id_guard(self):
		async def run():
			r = Registry()
			r.add("conv-1", "sess-A", "Claude", "req-1")
			assert r.remove("conv-1", "sess-A", request_id="req-OTHER") is None
			assert r.remove("conv-1", "sess-A", request_id="req-1") == "req-1"
		asyncio.run(run())

	def test_resolve_increments_total_answered(self):
		"""A successful resolve bumps total_answered (surfaced on /healthz and the
		Operator dashboard, which otherwise reads a permanently-zero counter)."""
		async def run():
			r = Registry()
			assert r.total_answered == 0
			r.add("conv-1", "sess-A", "Claude", "r1")
			r.resolve("conv-1", "r1", "the answer")
			assert r.total_answered == 1
			# A no-op resolve (unknown request_id) does not bump it.
			r.resolve("conv-1", "r1", "stale")
			assert r.total_answered == 1
		asyncio.run(run())

	def test_supersede_returns_none_when_slot_was_empty(self):
		async def run():
			r = Registry()
			fut, prior = r.add("conv-1", "sess-A", "Claude", "r1", return_superseded=True)
			assert prior is None
		asyncio.run(run())

	def test_supersede_is_per_session(self):
		async def run():
			r = Registry()
			fut_a = r.add("conv-1", "sess-A", "Claude", "r1")
			fut_b = r.add("conv-1", "sess-B", "Claude", "r2")
			assert not fut_a.cancelled()
			assert not fut_b.cancelled()
		asyncio.run(run())

	def test_resolve_unknown_returns_none(self):
		r = Registry()
		req_id = r.resolve("conv-1", "req-1", "orphan")
		assert req_id is None

	def test_remove_unknown_returns_none(self):
		r = Registry()
		req_id = r.remove("conv-1", "sess-A")
		assert req_id is None

	def test_all_pending_snapshot(self):
		async def run():
			r = Registry()
			r.add("conv-1", "sess-A", "Claude", "r1")
			r.add("conv-2", "sess-A", "Claude", "r2")
			r.add("conv-1", "sess-B", "Bob", "r3")
			pending = r.all_pending()
			assert len(pending) == 3
			req_ids = sorted(p.request_id for p in pending)
			assert req_ids == ["r1", "r2", "r3"]
		asyncio.run(run())

	def test_cancel_pending_for_conversation_cancels_matching_only(self):
		"""Cancel-on-spawn: cancel + pop every pending whose conversation_id matches; leave siblings on
		other conversations untouched. Returns the cancelled request_ids in registry-iteration order."""
		async def run():
			r = Registry()
			fut1 = r.add("conv-foo", "sess-A", "Claude", "req-1")
			fut2 = r.add("conv-foo", "sess-B", "Sparkles", "req-2")
			fut3 = r.add("conv-bar", "sess-A", "Claude", "req-3")

			cancelled = r.cancel_pending_for_conversation("conv-foo")

			assert sorted(cancelled) == ["req-1", "req-2"]
			assert fut1.cancelled() and fut2.cancelled()
			assert not fut3.cancelled()
			assert r.find_by_request_id("conv-foo", "req-1") is None
			assert r.find_by_request_id("conv-foo", "req-2") is None
			assert r.find_by_request_id("conv-bar", "req-3") is not None
		asyncio.run(run())

	def test_cancel_pending_for_conversation_no_match_returns_empty(self):
		async def run():
			r = Registry()
			r.add("conv-foo", "sess-A", "Claude", "req-1")
			cancelled = r.cancel_pending_for_conversation("conv-missing")
			assert cancelled == []
			# Existing entry untouched
			assert r.find_by_request_id("conv-foo", "req-1") is not None
		asyncio.run(run())

	def test_cancel_stale_pending_spares_live_session(self):
		"""Cancel-on-spawn (liveness-aware): a pending owned by a live session must
		survive; only pendings owned by a dead/unknown session are cancelled."""
		async def run():
			r = Registry()
			live = r.add("conv-foo", "s-live", "Claude", "req-live")
			dead = r.add("conv-foo", "s-dead", "Sparkles", "req-dead")

			cancelled = r.cancel_stale_pending_for_conversation("conv-foo", alive_session_ids={"s-live"})

			assert sorted(cancelled) == ["req-dead"]
			assert not live.cancelled()
			assert dead.cancelled()
			assert r.find_by_request_id("conv-foo", "req-live") is not None
			assert r.find_by_request_id("conv-foo", "req-dead") is None
		asyncio.run(run())

	def test_cancel_stale_pending_all_live_cancels_nothing(self):
		async def run():
			r = Registry()
			fut = r.add("conv-foo", "s-live", "Claude", "req-1")
			cancelled = r.cancel_stale_pending_for_conversation("conv-foo", alive_session_ids={"s-live"})
			assert cancelled == []
			assert not fut.cancelled()
			assert r.find_by_request_id("conv-foo", "req-1") is not None
		asyncio.run(run())

	def test_resolve_prepends_pending_notices(self):
		"""A convene notice attached to a pending (PendingRequest.notices) is
		prepended to the eventual human reply, separated by a blank line."""
		async def run():
			r = Registry()
			fut = r.add("conv-1", "sess-A", "Claude", "req-1")
			r.find_by_request_id("conv-1", "req-1").notices.append("You were convened into conv-9.")
			r.resolve("conv-1", "req-1", "yes, proceed")
			assert fut.result() == "You were convened into conv-9.\n\nyes, proceed"
		asyncio.run(run())



class TestPendingMirror:
	def test_add_calls_mirror_with_plus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add("conv-1", "sess-A", "Claude", "r1")
			assert calls == [("conv-1", 1)]
		asyncio.run(run())

	def test_resolve_calls_mirror_with_minus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add("conv-1", "sess-A", "Claude", "r1")
			calls.clear()
			r.resolve("conv-1", "r1", "ok")
			assert calls == [("conv-1", -1)]
		asyncio.run(run())

	def test_resolve_missing_does_not_call_mirror(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.resolve("conv-1", "r1", "ok")
			assert calls == []
		asyncio.run(run())

	def test_remove_calls_mirror_with_minus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add("conv-1", "sess-A", "Claude", "r1")
			calls.clear()
			r.remove("conv-1", "sess-A")
			assert calls == [("conv-1", -1)]
		asyncio.run(run())

	def test_supersede_via_add_emits_minus_one_then_plus_one(self):
		"""When add() supersedes an existing entry, the prior is cancelled (mirror -1)
		and the new is added (mirror +1). Two calls."""
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add("conv-1", "sess-A", "Claude", "r1")
			calls.clear()
			r.add("conv-1", "sess-A", "Claude", "r2")
			assert calls == [("conv-1", -1), ("conv-1", 1)]
		asyncio.run(run())

	def test_cancel_pending_for_conversation_calls_mirror_once_with_combined_delta(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add("conv-1", "sess-A", "A", "r1")
			r.add("conv-1", "sess-B", "B", "r2")
			r.add("conv-2", "sess-C", "C", "r3")
			calls.clear()
			r.cancel_pending_for_conversation("conv-1")
			assert calls == [("conv-1", -2)]
		asyncio.run(run())


class TestAwayModeCache:
	def test_update_global_away_cache(self):
		async def run():
			r = Registry()
			r.update_global_away_cache(True)
			assert r.global_away() is True
			r.update_global_away_cache(False)
			assert r.global_away() is False
		asyncio.run(run())


def test_conversation_member_schema():
	from server.registry import ConversationMember
	m = ConversationMember(
		cli_session_id="session-abc",
		sender="Claude-Win",
		cwd="C:\\Work\\Switchboard",
		surface="windows",
		joined_at=1000.0,
	)
	assert m.cli_session_id == "session-abc"
	assert m.alive is True               # default
	assert m.session_lost_permanently is False
	assert m.session_ended_at is None
	assert m.session_end_reason is None
	assert m.left_at is None
	assert m.last_seen_seq == 0


def test_conversation_active_ended_only():
	from server.registry import Conversation
	c = Conversation(
		id="conv-123",
		title="test",
	)
	assert c.state == "active"        # default
	assert c.continued_from is None
	assert c.members_active == {}
	assert c.members_history == []
	# `collab` attribute must not exist
	assert not hasattr(c, "collab")


def test_registry_session_routing_maps():
	from server.registry import Registry
	r = Registry()
	assert r.session_to_conversation_id == {}
	assert r.session_home_conversation_id == {}

	r.bind_session("session-x", "conv-1")
	assert r.session_to_conversation_id["session-x"] == "conv-1"

	r.set_session_home("session-x", "conv-1")
	assert r.session_home_conversation_id["session-x"] == "conv-1"


class TestActiveConversationsCount:
	def test_empty_registry_is_zero(self):
		r = Registry()
		assert r.active_conversations_count == 0

	def test_counts_only_active_state(self):
		from server.registry import Conversation
		r = Registry()
		r.conversations["c1"] = Conversation(id="c1", title="a", state="active")
		r.conversations["c2"] = Conversation(id="c2", title="b", state="ended")
		r.conversations["c3"] = Conversation(id="c3", title="c", state="active")
		assert r.active_conversations_count == 2

	def test_all_ended_is_zero(self):
		from server.registry import Conversation
		r = Registry()
		r.conversations["c1"] = Conversation(id="c1", title="a", state="ended")
		assert r.active_conversations_count == 0
