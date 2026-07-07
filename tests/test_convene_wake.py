"""Tests for _wake_convened: wake delivery for convened sessions (Task 6).

Per woken session, first match wins: a blocked message_and_await future
(queued in a conversation's wait_queue) resolves immediately with a convened
envelope; a pending ask_human gets the notice prepended to its eventual
answer; everyone else gets a hook-delivered notice queued on their session
record. Routing itself (_perform_convene) is covered by test_convene.py.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from server.conversation_ops import _perform_convene
from server.gateway.handlers import _wrap_wait_result
from server.logging_jsonl import JsonlLogger
from server.registry import ConversationMember
from server.session_registry import SessionRegistry
from tests.conftest import make_active_conversation, make_registry_with_loopback


@pytest.fixture
def logger(tmp_path):
	return JsonlLogger(str(tmp_path / "log.jsonl"))


@pytest.mark.asyncio
async def test_wake_resolves_blocked_message_and_await(logger):
	"""A session already a member of the target, queued in the target's
	wait_queue (blocked behind another speaker): convening it resolves the
	queued future with a convened envelope and drains the wait_queue entry."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-A", cwd="C:/A")
	session_registry.set_sender("s-A", "Claude-A")
	session_registry.record_session_start("s-B", cwd="C:/B")
	session_registry.set_sender("s-B", "Claude-B")

	conv = make_active_conversation(
		conversation_id="conv-X", member_session_id="s-A", sender="Claude-A", cwd="C:/A",
	)
	member_b = ConversationMember(
		cli_session_id="s-B", sender="Claude-B", cwd="C:/B", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-B"] = member_b
	registry.conversations["conv-X"] = conv
	registry.bind_session("s-A", "conv-X")
	registry.bind_session("s-B", "conv-X")

	future = asyncio.get_event_loop().create_future()
	wait_entry = {
		"member": member_b, "future": future, "waiting_kind": "msg_and_await", "block_position": time.monotonic(),
	}
	conv.wait_queue.append(wait_entry)
	member_b.last_seen_seq = len(conv.messages)

	cmd = {"session_ids": ["s-B"], "target": "conv-X", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["conversation_id"] == "conv-X"
	assert result["convened"] == ["Claude-B"]
	assert future.done()
	envelope = json.loads(future.result())
	assert envelope["status"] == "convened"
	assert envelope["conversation_id"] == "conv-X"
	assert "John convened" in envelope["log"]
	assert len(conv.wait_queue) == 0


@pytest.mark.asyncio
async def test_wake_attaches_notice_to_pending_ask(logger):
	"""A woken session with a pending ask_human gets the convene notice
	prepended to its eventual answer (registry.add + notices), not an
	immediately-resolved future."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-ask", cwd="C:/Q")
	session_registry.set_sender("s-ask", "Asker")

	pending_future = registry.add("some-other-conv", "s-ask", "Asker", "req-1")

	cmd = {"session_ids": ["s-ask"], "target": "new", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	pending = registry.find_by_request_id("some-other-conv", "req-1")
	assert pending is not None
	assert len(pending.notices) == 1
	assert "join_conversation(sender=" in pending.notices[0]
	assert f"ref='{result['conversation_id']}'" in pending.notices[0]
	assert not pending_future.done()


@pytest.mark.asyncio
async def test_wake_queues_notice_for_idle_session(logger):
	"""A woken session with no blocking structure at all gets a hook-delivered
	notice queued on its SessionRecord."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-idle", cwd="C:/I")
	session_registry.set_sender("s-idle", "Idle")

	cmd = {"session_ids": ["s-idle"], "target": "new", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	rec = session_registry.get("s-idle")
	assert len(rec.pending_notices) == 1
	assert "join_conversation(sender=" in rec.pending_notices[0]
	assert f"ref='{result['conversation_id']}'" in rec.pending_notices[0]


def test_wrap_wait_result_passes_envelopes_through():
	"""_wrap_wait_result must not re-wrap a convene-wake result that is already
	a status envelope (built directly by _wake_convened, not the normal
	plain-string wake payloads _wrap_wait_result otherwise translates)."""
	text = '{"status":"convened","conversation_id":"conv-9"}'
	assert _wrap_wait_result("conv-1", text) == text
