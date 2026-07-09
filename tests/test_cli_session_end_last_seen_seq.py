"""F-70: a member woken by cli_session_end's wake loop must have its
last_seen_seq advanced past the dormancy message, so the dormancy line is not
re-delivered on the member's next wake delta (parity with _wake_one_from)."""

from __future__ import annotations

import asyncio

import pytest

from server.cli_session_end import handle_session_end
from server.registry import Registry, ConversationMember
from tests.conftest import make_active_conversation


@pytest.mark.asyncio
async def test_woken_member_last_seen_seq_advances_past_dormancy(monkeypatch):
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-d1", member_session_id="s-leaver", sender="Leaver")
	# A second alive member who is blocked in message_and_await_agent.
	waiter = ConversationMember(
		cli_session_id="s-waiter", sender="Waiter", cwd="C:/Work/X", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-waiter"] = waiter
	registry.conversations["conv-d1"] = conv
	registry.bind_session("s-leaver", "conv-d1")
	registry.bind_session("s-waiter", "conv-d1")

	# Enqueue the waiter on the wait_queue with a future, mirroring the
	# message_and_await_agent wait_entry shape.
	loop = asyncio.get_running_loop()
	fut = loop.create_future()
	conv.wait_queue.append({
		"member": waiter,
		"future": fut,
		"waiting_kind": "msg_and_await",
		"block_position": 0.0,
	})

	await handle_session_end(registry, "s-leaver", "logout", now=lambda: "2026-06-13T00:00:00Z")

	assert fut.done()
	# The dormancy message was appended; the woken member must have seen up to it.
	assert waiter.last_seen_seq == len(conv.messages), \
		f"woken member last_seen_seq must point past the dormancy message; got {waiter.last_seen_seq} vs {len(conv.messages)}"


@pytest.mark.asyncio
async def test_session_end_wake_delivers_unseen_delta_plus_dormancy():
	# REV-111: a waiter whose cursor predates unseen message M must receive M
	# in the session-end wake payload, not just the dormancy line - the cursor
	# jump would otherwise hide M from every future wake and join log.
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-d2", member_session_id="s-leaver", sender="Leaver")
	waiter = ConversationMember(
		cli_session_id="s-waiter", sender="Waiter", cwd="C:/Work/X", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-waiter"] = waiter
	registry.conversations["conv-d2"] = conv
	registry.bind_session("s-leaver", "conv-d2")
	registry.bind_session("s-waiter", "conv-d2")

	# Unseen message M: landed after the waiter's cursor (last_seen_seq=0).
	conv.messages.append({
		"seq": 0, "sender": "Leaver", "type": "agent_msg",
		"text": "important context M", "timestamp": "2026-07-09T00:00:00+00:00",
	})
	fut = asyncio.get_running_loop().create_future()
	conv.wait_queue.append({
		"member": waiter, "future": fut, "waiting_kind": "msg_and_await", "block_position": 0.0,
	})

	await handle_session_end(registry, "s-leaver", "logout", now=lambda: "2026-07-09T00:00:01+00:00")

	assert fut.done()
	payload = fut.result()
	assert "Leaver: important context M" in payload
	assert "is now dormant" in payload
	assert payload.index("important context M") < payload.index("is now dormant")
	assert waiter.last_seen_seq == len(conv.messages)


@pytest.mark.asyncio
async def test_session_end_wake_excludes_waiters_own_messages():
	# Parity with _wake_one_from: the msg_and_await kind filters the waiter's
	# own emissions out of the delta.
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-d3", member_session_id="s-leaver", sender="Leaver")
	waiter = ConversationMember(
		cli_session_id="s-waiter", sender="Waiter", cwd="C:/Work/X", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-waiter"] = waiter
	registry.conversations["conv-d3"] = conv
	registry.bind_session("s-leaver", "conv-d3")
	registry.bind_session("s-waiter", "conv-d3")

	conv.messages.append({
		"seq": 0, "sender": "Waiter", "type": "agent_msg",
		"text": "my own words", "timestamp": "2026-07-09T00:00:00+00:00",
	})
	fut = asyncio.get_running_loop().create_future()
	conv.wait_queue.append({
		"member": waiter, "future": fut, "waiting_kind": "msg_and_await", "block_position": 0.0,
	})

	await handle_session_end(registry, "s-leaver", "logout", now=lambda: "2026-07-09T00:00:01+00:00")

	payload = fut.result()
	assert "my own words" not in payload
	assert "is now dormant" in payload
