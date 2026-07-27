"""Tests for post_agent_message: speak without blocking (spec D4)."""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.rate_limiter import RateLimiter
from tests.test_gateway_notify_human import RecordingBackend
from tests.test_message_and_await_agent import (
	_make_registry_with_two_alive_members,
	cfg,
	logger,
)


@pytest.mark.asyncio
async def test_post_returns_immediately_with_ok_envelope(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = json.loads(await asyncio.wait_for(handlers.post_agent_message(
		"Claude-A", "status: working on it", cli_session_id="s-A", cwd="C:/X"), timeout=2))
	assert result["status"] == "ok"
	assert result["conversation_id"] == "conv-1"
	assert result["msg_id"]
	assert "log" not in result  # nothing unseen: A is the only speaker so far
	(peer,) = result["peers"]
	assert peer["sender"] == "Claude-B"


@pytest.mark.asyncio
async def test_post_wakes_parked_waiter(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "opening", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)

	post = json.loads(await handlers.post_agent_message(
		"Claude-A", "ack, on it", cli_session_id="s-A", cwd="C:/X"))
	assert "Claude-B: opening" in post["log"]  # unseen delta excludes own message

	woken = json.loads(await asyncio.wait_for(task_b, timeout=2))
	assert woken["status"] == "ok"
	assert "Claude-A: ack, on it" in woken["log"]


@pytest.mark.asyncio
async def test_post_rate_limit_degrades_to_push_suppression(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter=RateLimiter(1))

	await handlers.post_agent_message("Claude-A", "one", cli_session_id="s-A", cwd="C:/X")
	await handlers.post_agent_message("Claude-A", "two", cli_session_id="s-A", cwd="C:/X")
	assert len(backend.channel_messages) == 2
	assert backend.push_suppressed == [False, True]


@pytest.mark.asyncio
async def test_post_requires_message_and_membership(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)

	empty = await handlers.post_agent_message("Claude-A", "", cli_session_id="s-A", cwd="C:/X")
	assert empty.startswith("ERROR:")
	unbound = await handlers.post_agent_message("Ghost", "hi", cli_session_id="s-ghost", cwd="C:/X")
	assert unbound.startswith("ERROR: not in any conversation")


@pytest.mark.asyncio
async def test_post_leaves_own_parked_wait_untouched(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task_a = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", "waiting for B", cli_session_id="s-A", cwd="C:/X"))
	await asyncio.sleep(0.05)
	conv = registry.conversations["conv-1"]
	assert len(conv.wait_queue) == 1

	await handlers.post_agent_message("Claude-A", "still here", cli_session_id="s-A", cwd="C:/X")
	await asyncio.sleep(0.05)
	assert not task_a.done()  # the parked wait survives a post from the same session
	assert len(conv.wait_queue) == 1

	task_a.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await task_a


@pytest.mark.asyncio
async def test_post_heals_fresh_spawn_membership(cfg, logger):
	registry, _ = _make_registry_with_two_alive_members()
	registry.bind_session("s-C", "conv-1")  # fresh-spawn state: bound, no member yet
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)

	result = json.loads(await handlers.post_agent_message(
		"Claude-C", "first action is a post", cli_session_id="s-C", cwd="C:/Z"))
	assert result["status"] == "ok"
	assert "s-C" in registry.conversations["conv-1"].members_active


@pytest.mark.asyncio
async def test_post_reports_write_failed_but_still_ok(cfg, logger):
	class ExplodingBackend(RecordingBackend):
		async def write_conversation_message(self, *args, **kwargs):
			raise RuntimeError("firebase down")

	registry, _ = _make_registry_with_two_alive_members()
	handlers = build_tool_handlers(cfg, registry, ExplodingBackend(), logger)

	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", "waiting", cli_session_id="s-B", cwd="C:/Y"))
	await asyncio.sleep(0.05)

	result = json.loads(await handlers.post_agent_message(
		"Claude-A", "hello", cli_session_id="s-A", cwd="C:/X"))
	assert result["status"] == "ok"
	assert result["write_failed"] is True
	assert "msg_id" not in result
	woken = json.loads(await asyncio.wait_for(task_b, timeout=2))
	assert "Claude-A: hello" in woken["log"]  # the wake succeeded despite the write failure


@pytest.mark.asyncio
async def test_wake_one_from_skips_excluded_live_entry_in_place(cfg, logger):
	"""Direct-construction test of the helper contract: with queue [B-live, A-live]
	and B excluded, A wakes and B's entry keeps its FIFO head position. (Through
	the public API the queue holds at most one live entry at a time - each speak
	wakes the previous waiter before parking - so the multi-entry case is built
	by hand.)"""
	from server.conversation_ops import _wake_one_from
	registry, _ = _make_registry_with_two_alive_members()
	conv = registry.conversations["conv-1"]
	a, b = conv.members_active["s-A"], conv.members_active["s-B"]
	fut_b = asyncio.get_event_loop().create_future()
	fut_a = asyncio.get_event_loop().create_future()
	conv.wait_queue.append({"member": b, "future": fut_b, "waiting_kind": "msg_and_await", "block_position": 0.0})
	conv.wait_queue.append({"member": a, "future": fut_a, "waiting_kind": "msg_and_await", "block_position": 1.0})

	assert _wake_one_from(conv, exclude_cli_session_id="s-B") is True
	assert fut_a.done() and not fut_b.done()  # A woke; B's excluded entry untouched
	assert len(conv.wait_queue) == 1
	assert conv.wait_queue[0]["member"].cli_session_id == "s-B"  # FIFO head preserved
	fut_b.cancel()


@pytest.mark.asyncio
async def test_wake_one_from_discards_dead_entries_of_excluded_session(cfg, logger):
	from server.conversation_ops import _wake_one_from
	registry, _ = _make_registry_with_two_alive_members()
	conv = registry.conversations["conv-1"]
	a = conv.members_active["s-A"]
	dead = asyncio.get_event_loop().create_future()
	dead.set_result("already resolved")
	conv.wait_queue.append({"member": a, "future": dead, "waiting_kind": "msg_and_await", "block_position": 0.0})

	assert _wake_one_from(conv, exclude_cli_session_id="s-A") is False
	assert len(conv.wait_queue) == 0  # dead entry discarded even though its session is excluded
