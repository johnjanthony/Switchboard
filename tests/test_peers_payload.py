"""Tests for the _peers_payload envelope helper and last_spoke_at tracking."""

from __future__ import annotations

import asyncio

import pytest

from server.conversation_ops import _peers_payload
from server.registry import Conversation, ConversationMember


def _member(sid: str, sender: str, alive: bool = True) -> ConversationMember:
	return ConversationMember(
		cli_session_id=sid, sender=sender, cwd="C:/X", surface="windows",
		joined_at=0.0, alive=alive,
	)


def _conv_with(members: list[ConversationMember]) -> Conversation:
	conv = Conversation(id="conv-1", title="test")
	for m in members:
		conv.members_active[m.cli_session_id] = m
	return conv


def test_excludes_caller_and_reports_states():
	a, b, c = _member("s-A", "A"), _member("s-B", "B"), _member("s-C", "C", alive=False)
	conv = _conv_with([a, b, c])
	peers = _peers_payload(conv, "s-A")
	assert {p["sender"] for p in peers} == {"B", "C"}
	states = {p["sender"]: p["state"] for p in peers}
	assert states == {"B": "alive", "C": "dormant"}


@pytest.mark.asyncio
async def test_waiting_true_only_for_parked_unresolved_futures():
	a, b, c = _member("s-A", "A"), _member("s-B", "B"), _member("s-C", "C")
	conv = _conv_with([a, b, c])
	live = asyncio.get_event_loop().create_future()
	dead = asyncio.get_event_loop().create_future()
	dead.set_result("done")
	conv.wait_queue.append({"member": b, "future": live, "waiting_kind": "msg_and_await", "block_position": 0.0})
	conv.wait_queue.append({"member": c, "future": dead, "waiting_kind": "msg_and_await", "block_position": 0.0})
	waiting = {p["sender"]: p["waiting"] for p in _peers_payload(conv, "s-A")}
	assert waiting == {"B": True, "C": False}
	live.cancel()


def test_last_spoke_at_field_default_none_and_passthrough():
	a, b = _member("s-A", "A"), _member("s-B", "B")
	b.last_spoke_at = 1753600000.0
	conv = _conv_with([a, b])
	(peer,) = _peers_payload(conv, "s-A")
	assert peer["last_spoke_at"] == 1753600000.0
	assert a.last_spoke_at is None
