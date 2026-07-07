"""Tests for combine_conversations."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)


def _make_member(
	session_id: str,
	sender: str,
	cwd: str = "C:/Work/X",
	alive: bool = True,
	session_lost_permanently: bool = False,
) -> ConversationMember:
	return ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=time.time(),
		alive=alive,
		session_lost_permanently=session_lost_permanently,
	)


def _two_conv_registry_alive() -> tuple[Registry, str, str]:
	"""Source has one alive member (s-A / Claude-A).
	Target has one alive member (s-T / Claude-T).
	Returns (registry, source_id, target_id)."""
	r = Registry()
	# Source
	src = Conversation(id="conv-src", title="Source Conv")
	mA = _make_member("s-A", "Claude-A")
	src.members_active["s-A"] = mA
	r.conversations["conv-src"] = src
	r.bind_session("s-A", "conv-src")
	r.set_session_home("s-A", "conv-src")
	# Target
	tgt = Conversation(id="conv-tgt", title="Target Conv")
	mT = _make_member("s-T", "Claude-T")
	tgt.members_active["s-T"] = mT
	r.conversations["conv-tgt"] = tgt
	r.bind_session("s-T", "conv-tgt")
	r.set_session_home("s-T", "conv-tgt")
	return r, "conv-src", "conv-tgt"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_combine_alive_members(tmp_path):
	"""Two convs, each with one alive member. Combine source into target.
	Verify: source.state == 'ended', source's member moved to target with
	binding rewritten."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry, src_id, tgt_id = _two_conv_registry_alive()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# Call combine as if from Claude-T (already in target)
	result = await handlers.combine_conversations(
		src_id,
		tgt_id,
		cli_session_id="s-T",
		cwd="C:/Work/X",
	)

	data = json.loads(result)
	assert data["status"] == "ok"
	assert data["source"] == src_id
	assert data["target"] == tgt_id
	assert "1 member" in data["detail"]

	src = registry.conversations[src_id]
	tgt = registry.conversations[tgt_id]

	# Source ended
	assert src.state == "ended"
	assert src.ended_at is not None

	# Claude-A moved to target
	assert "s-A" in tgt.members_active
	assert "s-A" not in src.members_active

	# Binding rewritten to target
	assert registry.session_to_conversation_id["s-A"] == tgt_id

	# Target has a merge system message
	sys_msgs = [m for m in tgt.messages if m.get("type") == "system"]
	assert any("Source Conv" in m["text"] for m in sys_msgs)

	# Source has a merge system message
	src_sys = [m for m in src.messages if m.get("type") == "system"]
	assert any("Target Conv" in m["text"] for m in src_sys)


@pytest.mark.asyncio
async def test_combine_resumes_dormant(tmp_path):
	"""Source has 1 dormant member. Combine into active target. Verify:
	- spawn-pending file written for the dormant member
	- member entry moved to target, bound + flipped alive (relaunch in flight)
	- source ends"""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()

	# Source with one dormant member
	src = Conversation(id="conv-src2", title="Dormant Source")
	mD = _make_member("s-dormant", "Claude-Dormant", alive=False)
	src.members_active["s-dormant"] = mD
	registry.conversations["conv-src2"] = src

	# Target with one alive member (caller)
	tgt = Conversation(id="conv-tgt2", title="Active Target")
	mT = _make_member("s-T2", "Claude-T2")
	tgt.members_active["s-T2"] = mT
	registry.conversations["conv-tgt2"] = tgt
	registry.bind_session("s-T2", "conv-tgt2")
	registry.set_session_home("s-T2", "conv-tgt2")

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
			patch("server.spawn.invoke_spawn_launcher", AsyncMock()) as mock_launch:
		result = await handlers.combine_conversations(
			"conv-src2",
			"conv-tgt2",
			cli_session_id="s-T2",
			cwd="C:/Work/X",
		)

	assert json.loads(result)["status"] == "ok"

	# Source ended
	assert registry.conversations["conv-src2"].state == "ended"

	# Dormant member moved to target, flipped alive (relaunch in flight)
	assert "s-dormant" in registry.conversations["conv-tgt2"].members_active
	assert registry.conversations["conv-tgt2"].members_active["s-dormant"].alive
	mock_launch.assert_awaited_once()

	# Session bound to target at relaunch time
	assert registry.session_to_conversation_id["s-dormant"] == "conv-tgt2"

	# Spawn-pending file written in the log's parent dir (tmp_path)
	pending_files = list(tmp_path.glob("spawn-pending-*.json"))
	assert len(pending_files) == 1

	payload = json.loads(pending_files[0].read_text(encoding="utf-8"))
	assert payload["type"] == "combine_resume"
	assert payload["target_conversation_id"] == "conv-tgt2"
	assert payload["source_conversation_id"] == "conv-src2"
	assert len(payload["agents"]) == 1
	assert payload["agents"][0]["cli_session_id"] == "s-dormant"


@pytest.mark.asyncio
async def test_combine_skips_permanently_lost(tmp_path):
	"""Source has 1 alive + 1 permanently_lost member. Combine.
	Verify: alive member moves, permanently-lost stays in source's members_active,
	source.state == 'ended' (still ends)."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()

	src = Conversation(id="conv-src3", title="Source With Lost")
	mAlive = _make_member("s-alive3", "Claude-Alive3")
	mLost = _make_member("s-lost3", "Claude-Lost3", session_lost_permanently=True)
	src.members_active["s-alive3"] = mAlive
	src.members_active["s-lost3"] = mLost
	registry.conversations["conv-src3"] = src
	registry.bind_session("s-alive3", "conv-src3")
	registry.bind_session("s-lost3", "conv-src3")

	tgt = Conversation(id="conv-tgt3", title="Target")
	mT = _make_member("s-T3", "Claude-T3")
	tgt.members_active["s-T3"] = mT
	registry.conversations["conv-tgt3"] = tgt
	registry.bind_session("s-T3", "conv-tgt3")
	registry.set_session_home("s-T3", "conv-tgt3")

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	result = await handlers.combine_conversations(
		"conv-src3",
		"conv-tgt3",
		cli_session_id="s-T3",
		cwd="C:/Work/X",
	)

	data = json.loads(result)
	assert data["status"] == "ok"
	assert "1 member" in data["detail"]

	src_conv = registry.conversations["conv-src3"]
	tgt_conv = registry.conversations["conv-tgt3"]

	# Source ends even though permanently-lost member remains
	assert src_conv.state == "ended"

	# Alive member moved to target
	assert "s-alive3" in tgt_conv.members_active
	assert "s-alive3" not in src_conv.members_active

	# Permanently-lost member stays in source
	assert "s-lost3" in src_conv.members_active


@pytest.mark.asyncio
async def test_combine_same_source_and_target_errors(tmp_path):
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()
	src = Conversation(id="conv-same", title="Same")
	mA = _make_member("s-same", "Claude-Same")
	src.members_active["s-same"] = mA
	registry.conversations["conv-same"] = src
	registry.bind_session("s-same", "conv-same")

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	result = await handlers.combine_conversations(
		"conv-same",
		"conv-same",
		cli_session_id="s-same",
		cwd="C:/Work/X",
	)

	assert "ERROR" in result
	assert "differ" in result


@pytest.mark.asyncio
async def test_combine_inactive_source_errors(tmp_path):
	"""Source is in 'ended' state → error."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()

	src = Conversation(id="conv-ended-src", title="Ended Source", state="ended")
	registry.conversations["conv-ended-src"] = src

	tgt = Conversation(id="conv-active-tgt4", title="Active Target")
	mT = _make_member("s-T4", "Claude-T4")
	tgt.members_active["s-T4"] = mT
	registry.conversations["conv-active-tgt4"] = tgt
	registry.bind_session("s-T4", "conv-active-tgt4")

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	result = await handlers.combine_conversations(
		"conv-ended-src",
		"conv-active-tgt4",
		cli_session_id="s-T4",
		cwd="C:/Work/X",
	)

	assert "ERROR" in result
	assert "not Active" in result


@pytest.mark.asyncio
async def test_combine_inactive_target_errors(tmp_path):
	"""Target is in 'ended' state → error."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()

	src = Conversation(id="conv-active-src5", title="Active Source")
	mA = _make_member("s-A5", "Claude-A5")
	src.members_active["s-A5"] = mA
	registry.conversations["conv-active-src5"] = src
	registry.bind_session("s-A5", "conv-active-src5")

	tgt = Conversation(id="conv-ended-tgt5", title="Ended Target", state="ended")
	registry.conversations["conv-ended-tgt5"] = tgt

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	result = await handlers.combine_conversations(
		"conv-active-src5",
		"conv-ended-tgt5",
		cli_session_id="s-A5",
		cwd="C:/Work/X",
	)

	assert "ERROR" in result
	assert "not Active" in result


@pytest.mark.asyncio
async def test_combine_source_with_only_permanently_lost_errors(tmp_path):
	"""Source has no movable members (only permanently_lost) → error."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry = Registry()

	src = Conversation(id="conv-all-lost", title="All Lost Source")
	mLost = _make_member("s-lost-only", "Claude-Lost-Only", session_lost_permanently=True)
	src.members_active["s-lost-only"] = mLost
	registry.conversations["conv-all-lost"] = src
	registry.bind_session("s-lost-only", "conv-all-lost")

	tgt = Conversation(id="conv-tgt-lost-test", title="Target")
	mT = _make_member("s-T-lost-test", "Claude-T-LT")
	tgt.members_active["s-T-lost-test"] = mT
	registry.conversations["conv-tgt-lost-test"] = tgt
	registry.bind_session("s-T-lost-test", "conv-tgt-lost-test")
	registry.set_session_home("s-T-lost-test", "conv-tgt-lost-test")

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	result = await handlers.combine_conversations(
		"conv-all-lost",
		"conv-tgt-lost-test",
		cli_session_id="s-T-lost-test",
		cwd="C:/Work/X",
	)

	assert "ERROR" in result
	assert "no movable members" in result


@pytest.mark.asyncio
async def test_combine_appends_system_messages(tmp_path):
	"""Source + target both get system markers in their message logs."""
	cfg = _cfg(tmp_path)
	backend = RecordingBackend()
	registry, src_id, tgt_id = _two_conv_registry_alive()

	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	await handlers.combine_conversations(
		src_id,
		tgt_id,
		cli_session_id="s-T",
		cwd="C:/Work/X",
	)

	src = registry.conversations[src_id]
	tgt = registry.conversations[tgt_id]

	# Source message: "Merged into '<target.title>'"
	src_sys = [m for m in src.messages if m.get("type") == "system"]
	assert any("Merged into" in m["text"] and "Target Conv" in m["text"] for m in src_sys)

	# Target message: "Merged with '<source.title>'. New members: Claude-A"
	tgt_sys = [m for m in tgt.messages if m.get("type") == "system"]
	assert any("Merged with" in m["text"] and "Source Conv" in m["text"] for m in tgt_sys)
	assert any("Claude-A" in m["text"] for m in tgt_sys)

	# Also verify the per-member combine intro was injected
	assert any("joined via combine" in m["text"] for m in tgt_sys)
