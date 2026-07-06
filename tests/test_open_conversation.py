"""Tests for open_conversation."""

from __future__ import annotations

import asyncio

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
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
def short_timeout_cfg(tmp_path):
	"""For tests that exercise the mint-path timeout — opener waits this long
	for a peer before giving up. Keep small so the timeout case runs fast."""
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0.3,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _make_registry_with_bound_session(session_id="s-1", conv_id="conv-abc", sender="Claude-A", cwd="C:/Work/X"):
	"""Registry with one conversation containing one member, session bound."""
	r = Registry()
	conv = Conversation(id=conv_id, title="original title")
	m = ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=0.0,
	)
	conv.members_active[session_id] = m
	r.conversations[conv_id] = conv
	r.bind_session(session_id, conv_id)
	return r, conv_id


@pytest.mark.asyncio
async def test_open_conversation_sets_pointer(cfg, logger):
	"""open_conversation sets registry.open_conversation_id to the caller's conv."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-A",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	assert result == f"ok. open_conversation = {conv_id}"
	assert r.open_conversation_id == conv_id


@pytest.mark.asyncio
async def test_open_conversation_updates_title(cfg, logger):
	"""Providing title updates conv.title."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	await handlers.open_conversation(
		"Claude-A",
		title="New Title",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	assert r.conversations[conv_id].title == "New Title"


@pytest.mark.asyncio
async def test_open_conversation_replaces_prior_open(cfg, logger):
	"""open_conversation flips the open pointer from one conv to another."""
	backend = RecordingBackend()
	r = Registry()

	# First conv — currently open
	conv1 = Conversation(id="conv-1", title="first")
	m1 = ConversationMember(cli_session_id="s-1", sender="Claude-A", cwd="C:/X", surface="windows", joined_at=0.0)
	conv1.members_active["s-1"] = m1
	r.conversations["conv-1"] = conv1
	r.bind_session("s-1", "conv-1")
	r.open_conversation_id = "conv-1"

	# Second conv
	conv2 = Conversation(id="conv-2", title="second")
	m2 = ConversationMember(cli_session_id="s-2", sender="Claude-B", cwd="C:/Y", surface="windows", joined_at=0.0)
	conv2.members_active["s-2"] = m2
	r.conversations["conv-2"] = conv2
	r.bind_session("s-2", "conv-2")

	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-B",
		cli_session_id="s-2",
		cwd="C:/Y",
	)

	assert result == "ok. open_conversation = conv-2"
	assert r.open_conversation_id == "conv-2"


@pytest.mark.asyncio
async def test_open_conversation_mints_when_unbound(cfg, logger):
	"""Session not bound to any conversation: open_conversation mints one and
	promotes it. The call blocks until a peer joins — we drive a peer-join
	concurrently to unblock it.

	Lets agents bootstrap a collab without first sending a real ask/notify."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	opener_task = asyncio.create_task(handlers.open_conversation(
		"Claude-X",
		cli_session_id="s-unbound",
		cwd="C:/Work/X",
	))
	# Let the opener mint + reach the await
	await asyncio.sleep(0.05)
	# A peer joins to unblock the opener
	peer_task = asyncio.create_task(handlers.enter_conversation(
		"Peer",
		cli_session_id="s-peer",
		cwd="/home/peer",
	))
	result = await asyncio.wait_for(opener_task, timeout=2.0)

	assert result.startswith("ok. open_conversation = ")
	conv_id = result.removeprefix("ok. open_conversation = ").split()[0]
	assert conv_id.startswith("conv-")
	assert r.open_conversation_id == conv_id
	assert conv_id in r.conversations
	conv = r.conversations[conv_id]
	assert "s-unbound" in conv.members_active
	assert conv.members_active["s-unbound"].cli_session_id == "s-unbound"
	assert r.session_to_conversation_id["s-unbound"] == conv_id

	# Clean up the peer (still blocked in intro queue)
	peer_task.cancel()
	try:
		await peer_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_open_conversation_unbound_honors_title(cfg, logger):
	"""When minting on unbound open, the supplied title is used. The call still
	blocks until a peer joins; drive a peer-join to unblock."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	opener_task = asyncio.create_task(handlers.open_conversation(
		"Claude-X",
		title="Collab bootstrap",
		cli_session_id="s-unbound",
		cwd="C:/Work/X",
	))
	await asyncio.sleep(0.05)
	peer_task = asyncio.create_task(handlers.enter_conversation(
		"Peer",
		cli_session_id="s-peer",
		cwd="/home/peer",
	))
	result = await asyncio.wait_for(opener_task, timeout=2.0)

	conv_id = result.removeprefix("ok. open_conversation = ").split()[0]
	assert r.conversations[conv_id].title == "Collab bootstrap"

	peer_task.cancel()
	try:
		await peer_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_open_conversation_renames_member_when_sender_changes(cfg, logger):
	"""Member currently keyed by OldName; calling with sender=NewName renames the key."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session(session_id="s-1", sender="OldName")
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"NewName",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	conv = r.conversations[conv_id]
	# Identity key is the session id and never changes on rename; only the
	# display sender is updated.
	assert "s-1" in conv.members_active
	assert conv.members_active["s-1"].sender == "NewName"
	assert result.startswith("ok.")


@pytest.mark.asyncio
async def test_open_conversation_no_rename_when_sender_unchanged(cfg, logger):
	"""When sender matches existing key, members_active is unchanged (no spurious rename)."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_bound_session(session_id="s-1", sender="Claude-A")
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-A",
		cli_session_id="s-1",
		cwd="C:/Work/X",
	)

	conv = r.conversations[conv_id]
	assert "s-1" in conv.members_active
	assert conv.members_active["s-1"].sender == "Claude-A"
	assert len(conv.members_active) == 1
	assert result.startswith("ok.")


@pytest.mark.asyncio
async def test_open_conversation_mint_returns_peer_name_on_wake(cfg, logger):
	"""When a peer joins via enter_conversation, the opener's wake payload
	identifies the joiner by sender so the opener can greet by name."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	opener_task = asyncio.create_task(handlers.open_conversation(
		"Opener",
		cli_session_id="s-opener",
		cwd="C:/Work/O",
	))
	await asyncio.sleep(0.05)
	peer_task = asyncio.create_task(handlers.enter_conversation(
		"Joiner",
		cli_session_id="s-joiner",
		cwd="/home/joiner",
	))
	result = await asyncio.wait_for(opener_task, timeout=2.0)

	assert "Joiner" in result, f"Expected joiner sender in wake payload, got: {result!r}"
	assert "open_conversation" in result

	peer_task.cancel()
	try:
		await peer_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_open_conversation_mint_times_out_and_ends_conv(short_timeout_cfg, logger):
	"""If no peer joins within timeout_seconds, the opener gets __TIMEOUT__,
	the conversation is force-ended (state=ended), and the open marker is
	cleared so the orphan conv doesn't leak."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(short_timeout_cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Lonely",
		cli_session_id="s-lonely",
		cwd="C:/Work/L",
	)

	assert result == "__TIMEOUT__"
	# Marker should be cleared so subsequent agents don't try to join an orphan
	assert r.open_conversation_id is None
	# The minted conv should be marked ended
	ended = [c for c in r.conversations.values() if c.state == "ended"]
	assert len(ended) >= 1, "Timed-out mint conv should be marked ended"


@pytest.mark.asyncio
async def test_open_conversation_mint_blocks_then_unblocks_on_peer_join(cfg, logger):
	"""The opener call should not complete before a peer joins. Verify the
	blocking semantics by checking the opener task is still pending after a
	short delay with no peer activity."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	opener_task = asyncio.create_task(handlers.open_conversation(
		"Opener",
		cli_session_id="s-opener",
		cwd="C:/Work/O",
	))
	await asyncio.sleep(0.1)
	# Opener is still blocked — no peer has joined yet
	assert not opener_task.done(), "open_conversation should block until a peer joins"

	# Now drive a peer join
	peer_task = asyncio.create_task(handlers.enter_conversation(
		"Peer",
		cli_session_id="s-peer",
		cwd="/home/peer",
	))
	result = await asyncio.wait_for(opener_task, timeout=2.0)
	assert result.startswith("ok. open_conversation = ")

	peer_task.cancel()
	try:
		await peer_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_open_conversation_errors_missing_cli_session_id(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	r = Registry()
	handlers = build_tool_handlers(cfg, r, backend, logger)

	result = await handlers.open_conversation(
		"Claude-A",
		cwd="C:/Work/X",
	)

	assert result.startswith("ERROR: cli_session_id required")
