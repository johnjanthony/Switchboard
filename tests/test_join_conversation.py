"""Tests for the join_conversation tool handler: idempotent, non-blocking
join/mint with synchronous unseen-history delivery."""

from __future__ import annotations

import asyncio
import json

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.conftest import make_registry_with_loopback
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _handlers(cfg, registry, backend, logger):
	return build_tool_handlers(cfg, registry, backend, logger)


@pytest.mark.asyncio
async def test_ref_absent_mints_and_promotes(cfg, logger):
	"""Unbound caller, no open marker, ref=None: mints a fresh conversation and
	promotes it to the open singleton in one non-blocking call."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	handlers = _handlers(cfg, r, backend, logger)

	result = await handlers.join_conversation(
		"Claude-A",
		cli_session_id="s-a",
		cwd="C:/Work/A",
	)

	data = json.loads(result)
	assert data["status"] == "ok"
	assert data["minted"] is True
	assert data["peers"] == []
	conv_id = data["conversation_id"]
	assert r.open_conversation_id == conv_id
	assert r.session_to_conversation_id["s-a"] == conv_id
	assert "s-a" in r.conversations[conv_id].members_active


@pytest.mark.asyncio
async def test_ref_absent_second_joiner_lands_with_first(cfg, logger):
	"""A mints via ref=None; unbound B also calls with ref=None and lands in the
	same room (the open marker pairs them up) rather than minting a second one."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	handlers = _handlers(cfg, r, backend, logger)

	result_a = await handlers.join_conversation("A", cli_session_id="s-a", cwd="C:/Work/A")
	data_a = json.loads(result_a)
	conv_id = data_a["conversation_id"]

	# Seed a message so the "full history" claim for B is meaningful.
	conv = r.conversations[conv_id]
	conv.messages.append({
		"seq": 0, "sender": "A", "type": "agent_msg", "text": "hello from A",
		"timestamp": "2026-01-01T00:00:00+00:00", "title": None,
	})

	result_b = await handlers.join_conversation("B", cli_session_id="s-b", cwd="C:/Work/B")
	data_b = json.loads(result_b)

	assert data_b["conversation_id"] == conv_id
	assert data_b["peers"] == ["A"]
	assert "hello from A" in data_b["log"]
	assert len(r.conversations) == 1


@pytest.mark.asyncio
async def test_ref_given_joins_that_conversation(cfg, logger):
	"""Unbound caller joins an existing active conversation by explicit ref."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	conv = Conversation(id="conv-existing", title="existing")
	host = ConversationMember(
		cli_session_id="s-host", sender="Host", cwd="C:/Work/H", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-host"] = host
	conv.messages.append({
		"seq": 0, "sender": "Host", "type": "agent_msg", "text": "welcome",
		"timestamp": "2026-01-01T00:00:00+00:00", "title": None,
	})
	r.conversations["conv-existing"] = conv
	r.bind_session("s-host", "conv-existing")
	handlers = _handlers(cfg, r, backend, logger)

	result = await handlers.join_conversation(
		"Guest",
		ref="conv-existing",
		cli_session_id="s-guest",
		cwd="C:/Work/G",
	)

	data = json.loads(result)
	assert data["status"] == "ok"
	assert data["conversation_id"] == "conv-existing"
	assert data["peers"] == ["Host"]
	assert "welcome" in data["log"]
	assert "s-guest" in conv.members_active
	assert r.session_to_conversation_id["s-guest"] == "conv-existing"


@pytest.mark.asyncio
async def test_ref_invalid_returns_error_string(cfg, logger):
	"""A ref that doesn't resolve to an Active conversation returns a bare
	ERROR: string, not a JSON envelope."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	handlers = _handlers(cfg, r, backend, logger)

	result = await handlers.join_conversation(
		"Claude-A",
		ref="conv-nope",
		cli_session_id="s-a",
		cwd="C:/Work/A",
	)

	assert result.startswith("ERROR:")
	with pytest.raises(json.JSONDecodeError):
		json.loads(result)


@pytest.mark.asyncio
async def test_already_member_is_idempotent_and_returns_delta(cfg, logger):
	"""Re-joining an already-bound conversation is a no-op on membership but
	still returns the unseen delta, and advances last_seen_seq."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	conv = Conversation(id="conv-1", title="test")
	peer = ConversationMember(
		cli_session_id="s-peer", sender="Peer", cwd="C:/Work/P", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-peer"] = peer
	r.conversations["conv-1"] = conv
	r.bind_session("s-peer", "conv-1")
	handlers = _handlers(cfg, r, backend, logger)

	# First join: Mover becomes a member of conv-1.
	first = await handlers.join_conversation(
		"Mover", ref="conv-1", cli_session_id="s-mover", cwd="C:/Work/M",
	)
	first_data = json.loads(first)
	assert first_data.get("already_member") is None
	member_count_before = len(conv.members_active)

	# Peer speaks; since Mover is alive it enqueues Peer rather than replying
	# immediately (message_and_await_agent blocks with two alive members).
	peer_task = asyncio.create_task(handlers.message_and_await_agent(
		"Peer", message="peer says hi", cli_session_id="s-peer", cwd="C:/Work/P",
	))
	await asyncio.sleep(0.05)

	# Second join: idempotent, returns only the delta since the first join.
	second = await handlers.join_conversation(
		"Mover", ref="conv-1", cli_session_id="s-mover", cwd="C:/Work/M",
	)
	second_data = json.loads(second)

	assert second_data["already_member"] is True
	assert "peer says hi" in second_data["log"]
	assert "welcome" not in second_data.get("log", "")
	assert len(conv.members_active) == member_count_before
	assert conv.members_active["s-mover"].last_seen_seq == len(conv.messages)

	peer_task.cancel()
	try:
		await peer_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_bound_elsewhere_migrates(cfg, logger):
	"""Caller bound to solo conversation X joins ref=Y: moves to Y, X ends
	(no members remain), and the result reflects sender disambiguation."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	from server.conversation_ops import _create_active_conversation_for

	source_id = await _create_active_conversation_for(
		r, cli_session_id="s-c", cwd="C:/Work/X", sender="Claude", backend=backend,
	)

	target = Conversation(id="conv-y", title="target")
	existing = ConversationMember(
		cli_session_id="s-existing", sender="Claude", cwd="C:/Work/Y", surface="windows", joined_at=0.0,
	)
	target.members_active["s-existing"] = existing
	target.messages.append({
		"seq": 0, "sender": "Claude", "type": "agent_msg", "text": "hello from Y",
		"timestamp": "2026-01-01T00:00:00+00:00", "title": None,
	})
	r.conversations["conv-y"] = target
	handlers = _handlers(cfg, r, backend, logger)

	result = await handlers.join_conversation(
		"Claude",
		ref="conv-y",
		cli_session_id="s-c",
		cwd="C:/Work/X2",
	)

	data = json.loads(result)
	assert data["conversation_id"] == "conv-y"
	assert data["sender"] == "Claude 2"  # disambiguated against the existing "Claude"
	assert "hello from Y" in data["log"]

	# Caller now a member of Y, not X.
	assert "s-c" in target.members_active
	assert r.session_to_conversation_id["s-c"] == "conv-y"

	source = r.conversations[source_id]
	assert "s-c" not in source.members_active
	assert source.state == "ended"


# test_never_blocks: every case above returns without any peer speaking via
# wait_for/futures on the join_conversation call itself (unlike open_conversation
# and enter_conversation, which block on a future). No separate test is needed;
# the absence of any await-a-future pattern in the handler is the proof.


@pytest.mark.asyncio
async def test_join_wakes_lobby_opener(cfg, logger):
	"""A peer blocked in the mint-path open_conversation (open_peer_future set)
	is woken when another agent join_conversation()s into that room."""
	backend = RecordingBackend()
	r = make_registry_with_loopback()
	handlers = _handlers(cfg, r, backend, logger)

	opener_task = asyncio.create_task(handlers.open_conversation(
		"Opener",
		cli_session_id="s-opener",
		cwd="C:/Work/O",
	))
	await asyncio.sleep(0.05)

	conv_id = next(iter(r.conversations))
	conv = r.conversations[conv_id]
	future = conv.open_peer_future
	assert future is not None
	assert not future.done()

	result = await handlers.join_conversation(
		"Joiner",
		ref=conv_id,
		cli_session_id="s-joiner",
		cwd="C:/Work/J",
	)
	data = json.loads(result)
	assert data["status"] == "ok"

	opener_result = await asyncio.wait_for(opener_task, timeout=2.0)
	assert future.done()
	assert "Joiner" in opener_result
