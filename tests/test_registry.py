"""Tests for the pending-request registry."""

import asyncio
import json
from datetime import datetime

import pytest

from server.registry import PendingRequest, Registry


class TestPendingByKey:
	def test_add_creates_entry(self):
		async def run():
			r = Registry()
			fut = r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			assert isinstance(fut, asyncio.Future)
			assert r.get(("conv-1", "Claude")) is not None
			assert r.get(("conv-1", "Claude")).request_id == "r1"
		asyncio.run(run())

	def test_supersede_cancels_old_future(self):
		async def run():
			r = Registry()
			old_fut = r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			r.add(conversation_id="conv-1", sender="Claude", request_id="r2")
			assert old_fut.cancelled()
			assert r.get(("conv-1", "Claude")).request_id == "r2"
		asyncio.run(run())

	def test_supersede_returns_prior_request_id(self):
		async def run():
			r = Registry()
			r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			fut, prior = r.add(conversation_id="conv-1", sender="Claude", request_id="r2",
			                   return_superseded=True)
			assert prior == "r1"
			assert isinstance(fut, asyncio.Future)
		asyncio.run(run())

	def test_supersede_returns_none_when_slot_was_empty(self):
		async def run():
			r = Registry()
			fut, prior = r.add(conversation_id="conv-1", sender="Claude", request_id="r1",
			                   return_superseded=True)
			assert prior is None
		asyncio.run(run())

	def test_supersede_is_per_sender(self):
		async def run():
			r = Registry()
			fut_a = r.add(conversation_id="conv-1", sender="Alice", request_id="r1")
			fut_b = r.add(conversation_id="conv-1", sender="Bob", request_id="r2")
			assert not fut_a.cancelled()
			assert not fut_b.cancelled()
		asyncio.run(run())

	def test_resolve_by_key(self):
		async def run():
			r = Registry()
			fut = r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			req_id = r.resolve(conversation_id="conv-1", sender="Claude", text="answer")
			assert req_id == "r1"
			assert fut.result() == "answer"
			assert r.get(("conv-1", "Claude")) is None
		asyncio.run(run())

	def test_resolve_unknown_returns_none(self):
		r = Registry()
		req_id = r.resolve(conversation_id="conv-1", sender="Claude", text="orphan")
		assert req_id is None

	def test_remove_by_key(self):
		async def run():
			r = Registry()
			fut = r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			req_id = r.remove(conversation_id="conv-1", sender="Claude")
			assert req_id == "r1"
			assert fut.cancelled()
			assert r.get(("conv-1", "Claude")) is None
		asyncio.run(run())

	def test_remove_unknown_returns_none(self):
		r = Registry()
		req_id = r.remove(conversation_id="conv-1", sender="Claude")
		assert req_id is None

	def test_all_pending_snapshot(self):
		async def run():
			r = Registry()
			r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			r.add(conversation_id="conv-2", sender="Claude", request_id="r2")
			r.add(conversation_id="conv-1", sender="Bob", request_id="r3")
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
			fut1 = r.add(conversation_id="conv-foo", sender="Claude", request_id="req-1")
			fut2 = r.add(conversation_id="conv-foo", sender="Sparkles", request_id="req-2")
			fut3 = r.add(conversation_id="conv-bar", sender="Claude", request_id="req-3")

			cancelled = r.cancel_pending_for_conversation("conv-foo")

			assert sorted(cancelled) == ["req-1", "req-2"]
			assert fut1.cancelled() and fut2.cancelled()
			assert not fut3.cancelled()
			assert r.get(("conv-foo", "Claude")) is None
			assert r.get(("conv-foo", "Sparkles")) is None
			assert r.get(("conv-bar", "Claude")) is not None
		asyncio.run(run())

	def test_cancel_pending_for_conversation_no_match_returns_empty(self):
		async def run():
			r = Registry()
			r.add(conversation_id="conv-foo", sender="Claude", request_id="req-1")
			cancelled = r.cancel_pending_for_conversation("conv-missing")
			assert cancelled == []
			# Existing entry untouched
			assert r.get(("conv-foo", "Claude")) is not None
		asyncio.run(run())



class TestPendingMirror:
	def test_add_calls_mirror_with_plus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			assert calls == [("conv-1", 1)]
		asyncio.run(run())

	def test_resolve_calls_mirror_with_minus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			calls.clear()
			r.resolve("conv-1", "Claude", "ok")
			assert calls == [("conv-1", -1)]
		asyncio.run(run())

	def test_resolve_missing_does_not_call_mirror(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.resolve("conv-1", "Claude", "ok")
			assert calls == []
		asyncio.run(run())

	def test_remove_calls_mirror_with_minus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			calls.clear()
			r.remove("conv-1", "Claude")
			assert calls == [("conv-1", -1)]
		asyncio.run(run())

	def test_supersede_via_add_emits_minus_one_then_plus_one(self):
		"""When add() supersedes an existing entry, the prior is cancelled (mirror -1)
		and the new is added (mirror +1). Two calls."""
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add(conversation_id="conv-1", sender="Claude", request_id="r1")
			calls.clear()
			r.add(conversation_id="conv-1", sender="Claude", request_id="r2")
			assert calls == [("conv-1", -1), ("conv-1", 1)]
		asyncio.run(run())

	def test_cancel_pending_for_conversation_calls_mirror_once_with_combined_delta(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda conversation_id, delta: calls.append((conversation_id, delta)))
			r.add(conversation_id="conv-1", sender="A", request_id="r1")
			r.add(conversation_id="conv-1", sender="B", request_id="r2")
			r.add(conversation_id="conv-2", sender="C", request_id="r3")
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
	assert r.open_conversation_id is None

	r.bind_session("session-x", "conv-1")
	assert r.session_to_conversation_id["session-x"] == "conv-1"

	r.set_session_home("session-x", "conv-1")
	assert r.session_home_conversation_id["session-x"] == "conv-1"

	r.open_conversation_id = "conv-1"
	assert r.open_conversation_id == "conv-1"
