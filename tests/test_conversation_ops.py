"""Tests for conversation-mutation helpers."""

import asyncio

import pytest

from server.conversation_ops import _create_active_conversation_for, _wake_one_from
from server.registry import ConversationMember, Registry
from tests.conftest import make_active_conversation, make_registry_with_loopback


def test_create_active_conversation_for_windows():
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-1", cwd="C:/Work/X", sender="Claude",
		)
		conv = r.conversations[conv_id]
		assert conv.state == "active"
		assert len(conv.members_active) == 1
		member = next(iter(conv.members_active.values()))
		assert member.cli_session_id == "s-1"
		assert member.sender == "Claude"
		assert member.surface == "windows"  # C:/Work/X infers windows
		assert r.session_to_conversation_id["s-1"] == conv_id
		assert r.session_home_conversation_id["s-1"] == conv_id
	asyncio.run(run())


def test_create_active_conversation_for_wsl_mnt():
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-2", cwd="/mnt/c/work", sender="Claude-WSL",
		)
		member = next(iter(r.conversations[conv_id].members_active.values()))
		assert member.surface == "wsl"
	asyncio.run(run())


def test_create_active_conversation_for_wsl_home():
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-3", cwd="/home/john/work/switchboard", sender="Claude",
		)
		member = next(iter(r.conversations[conv_id].members_active.values()))
		assert member.surface == "wsl"
	asyncio.run(run())


def test_create_active_conversation_for_windows_backslash():
	"""Cwd with Windows backslashes also infers windows."""
	async def run():
		r = Registry()
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-4", cwd=r"C:\Work\X", sender="Claude",
		)
		member = next(iter(r.conversations[conv_id].members_active.values()))
		assert member.surface == "windows"
	asyncio.run(run())


def test_does_not_overwrite_existing_home():
	"""If home pointer already exists for this session, leave it alone."""
	async def run():
		r = Registry()
		r.set_session_home("s-1", "conv-existing-home")
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-1", cwd="C:/X", sender="Claude",
		)
		# Binding goes to new conv; home pointer stays
		assert r.session_to_conversation_id["s-1"] == conv_id
		assert r.session_home_conversation_id["s-1"] == "conv-existing-home"
	asyncio.run(run())


def test_create_active_conversation_for_same_session_race():
	"""5 concurrent calls with the same cli_session_id must produce exactly one
	conversation and all return the same conv_id (per-session lock + double-check)."""
	async def run():
		r = Registry()
		results = await asyncio.gather(*[
			_create_active_conversation_for(r, cli_session_id="s-race", cwd="C:/Work/X", sender="Claude")
			for _ in range(5)
		])
		# All calls return the same conv_id
		assert len(set(results)) == 1
		# Exactly one conversation was created
		assert len(r.conversations) == 1
		conv_id = results[0]
		assert r.session_to_conversation_id["s-race"] == conv_id
	asyncio.run(run())


def test_add_member_sender_collision_disambiguates():
	"""When two sessions join the same conversation with the same sender name,
	the second gets an auto-numbered space-suffix (e.g. 'Claude 2')."""
	from server.conversation_ops import _add_member

	async def run():
		r = Registry()
		# Create a conversation first (session s-a joins as "Claude")
		conv_id = await _create_active_conversation_for(
			r, cli_session_id="s-a", cwd="C:/Work/X", sender="Claude",
		)
		conv = r.conversations[conv_id]
		assert "s-a" in conv.members_active

		# Add a second member with the same desired sender name
		await _add_member(r, conv_id, "s-b", "Claude", "C:/Work/X")

		# Both members exist, both alive, distinct session ids
		assert "s-a" in conv.members_active
		assert "s-b" in conv.members_active
		assert conv.members_active["s-a"].sender == "Claude"
		assert conv.members_active["s-b"].sender == "Claude 2"
	asyncio.run(run())


@pytest.mark.anyio
async def test_members_active_keyed_by_session_id():
	registry = Registry()
	conv_id = await _create_active_conversation_for(registry, "sess-A", "C:/Work/X", "Claude")
	conv = registry.conversations[conv_id]
	assert "sess-A" in conv.members_active
	assert conv.members_active["sess-A"].sender == "Claude"


@pytest.mark.anyio
async def test_same_sender_two_sessions_disambiguates_display_only():
	from server.conversation_ops import _add_member

	registry = Registry()
	conv_id = await _create_active_conversation_for(registry, "sess-A", "C:/Work/X", "Claude")
	conv = registry.conversations[conv_id]
	await _add_member(registry, conv_id, "sess-B", "Claude", "C:/Work/Y")
	assert set(conv.members_active.keys()) == {"sess-A", "sess-B"}
	assert conv.members_active["sess-A"].sender == "Claude"
	assert conv.members_active["sess-B"].sender == "Claude 2"


async def test_create_active_conversation_default_origin_is_fallback():
	registry = make_registry_with_loopback()
	conv_id = await _create_active_conversation_for(registry, "s-1", "C:/Work/X", "Claude")
	assert registry.conversations[conv_id].origin == "fallback"


async def test_create_active_conversation_origin_join():
	registry = make_registry_with_loopback()
	conv_id = await _create_active_conversation_for(registry, "s-1", "C:/Work/X", "Claude", origin="join")
	assert registry.conversations[conv_id].origin == "join"


def test_wake_one_from_skips_done_future_and_wakes_next():
	# REV-101: wait_for's timeout or cancel completes a waiter's future before
	# the waiter reacquires conv.lock to dequeue itself. A speaker holding the
	# lock pops that dead entry and must go on to wake the next live waiter,
	# not silently wake nobody.
	async def run():
		conv = make_active_conversation(conversation_id="conv-w1", member_session_id="s-dead", sender="Dead")
		alive = ConversationMember(
			cli_session_id="s-alive", sender="Alive", cwd="C:/Work/X", surface="windows", joined_at=0.0,
		)
		conv.members_active["s-alive"] = alive
		dead = conv.members_active["s-dead"]
		loop = asyncio.get_running_loop()
		dead_fut = loop.create_future()
		dead_fut.cancel()  # what wait_for does internally on timeout or MCP cancel
		live_fut = loop.create_future()
		conv.wait_queue.append({"member": dead, "future": dead_fut, "waiting_kind": "msg_and_await", "block_position": 0.0})
		conv.wait_queue.append({"member": alive, "future": live_fut, "waiting_kind": "msg_and_await", "block_position": 1.0})
		conv.messages.append({"seq": 0, "sender": "Dead", "type": "agent_msg", "text": "hello", "timestamp": "2026-07-09T00:00:00+00:00"})
		assert _wake_one_from(conv) is True
		assert live_fut.done() and live_fut.result() == "Dead: hello"
		assert len(conv.wait_queue) == 0
		assert alive.last_seen_seq == len(conv.messages)
		assert dead.last_seen_seq == 0  # nothing was delivered to the dead waiter
	asyncio.run(run())


def test_wake_one_from_returns_false_when_all_waiters_dead():
	async def run():
		conv = make_active_conversation(conversation_id="conv-w2")
		member = conv.members_active["s-1"]
		loop = asyncio.get_running_loop()
		f1, f2 = loop.create_future(), loop.create_future()
		f1.cancel()
		f2.set_result("already resolved elsewhere")
		conv.wait_queue.append({"member": member, "future": f1, "waiting_kind": "msg_and_await", "block_position": 0.0})
		conv.wait_queue.append({"member": member, "future": f2, "waiting_kind": "msg_and_await", "block_position": 1.0})
		assert _wake_one_from(conv) is False
		assert len(conv.wait_queue) == 0  # dead entries drained, not left to eat future wakes
		assert member.last_seen_seq == 0
	asyncio.run(run())


def test_wake_one_from_returns_false_on_empty_queue():
	conv = make_active_conversation(conversation_id="conv-w3")
	assert _wake_one_from(conv) is False
