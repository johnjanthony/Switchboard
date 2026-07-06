"""End-to-end test: fresh spawn → agent makes call → SessionEnd → resume."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend() -> MagicMock:
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="push-key-1")
	backend.write_conversation_member = AsyncMock()
	backend.remove_conversation_member = AsyncMock()
	backend.set_conversation_state = AsyncMock()
	backend.set_conversation_last_activity = AsyncMock()
	backend.set_open_conversation_id = AsyncMock()
	backend.set_session_home = AsyncMock()
	backend.set_global_away_mode = AsyncMock()
	return backend


def _make_config(tmp_path: Path, spawn_root: Path | None = None) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=spawn_root,
		wsl_home_resolved="/home/john",
		wsl_spawn_root_segment="work",
	)


def _find_pending_files(tmp_path: Path) -> list[Path]:
	return list(tmp_path.glob("spawn-pending-*.json"))


def _read_pending(tmp_path: Path) -> dict:
	files = _find_pending_files(tmp_path)
	assert len(files) == 1, f"Expected 1 pending file, found {len(files)}: {files}"
	return json.loads(files[0].read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _assume_logged_in(monkeypatch):
	"""Default precondition: a user is logged in (no real quser call)."""
	from server.spawn import SpawnHandler
	monkeypatch.setattr(
		SpawnHandler, "_user_has_interactive_session", AsyncMock(return_value=True)
	)


# ---------------------------------------------------------------------------
# Test 1: fresh spawn → pending file written → agent's first call routes to pre-bound conv
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_fresh_spawn_then_first_call(tmp_path):
	"""Fresh single-agent spawn → SpawnHandler.handle_fresh writes pending file →
	agent's first call (notify_human) with the pre-bound session_id routes to the
	pre-bound conversation (no second conv minted)."""
	from server.spawn import SpawnHandler

	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = _make_config(tmp_path, spawn_root=spawn_root)
	backend = _make_backend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Fire handle_fresh with a no-op launcher
	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, logger, registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "projects",
			"prompt": "Do stuff",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	# Inspect spawn-pending file for the pre-bound session + conv
	pending = _read_pending(tmp_path)
	assert pending["type"] == "fresh"
	assert len(pending["agents"]) == 1
	agent_entry = pending["agents"][0]
	pre_bound_session = agent_entry["cli_session_id"]
	conv_id = pending["conversation_id"]

	# Confirm pre-binding in registry
	assert registry.session_to_conversation_id.get(pre_bound_session) == conv_id
	assert conv_id in registry.conversations

	conv_count_after_spawn = len(registry.conversations)

	# Simulate agent's first call using the pre-bound session_id + cwd from pending
	tool_backend = RecordingBackend()
	tool_logger = JsonlLogger(cfg.log_path)
	tool_cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=cfg.log_path,
	)
	handlers = build_tool_handlers(tool_cfg, registry, tool_backend, tool_logger)

	result = await handlers.notify_human(
		"starting work",
		sender="claude",
		cli_session_id=pre_bound_session,
		cwd=agent_entry["project_path"],
	)
	assert result == "ok", f"notify_human failed: {result}"

	# No new conversation should have been minted (pre-bound session already in conv)
	assert len(registry.conversations) == conv_count_after_spawn, \
		"First call must NOT mint a new conversation when session is pre-bound"

	# Session is still bound to the same conv
	assert registry.session_to_conversation_id[pre_bound_session] == conv_id


# ---------------------------------------------------------------------------
# Test 2: session end → resume creates continuation conv
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_session_end_then_resume(tmp_path):
	"""1. Spawn fresh agent. 2. Simulate SessionEnd (mark member dormant). 3. Resume.
	4. Verify continuation conv created with continued_from set, session pre-bound."""
	from server.spawn import SpawnHandler
	from server.cli_session_end import handle_session_end

	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = _make_config(tmp_path, spawn_root=spawn_root)
	backend = _make_backend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Step 1: fresh spawn
	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, logger, registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "projects",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	pending = _read_pending(tmp_path)
	pre_bound_session = pending["agents"][0]["cli_session_id"]
	conv_id = pending["conversation_id"]
	conv = registry.conversations[conv_id]

	# The agent needs to be a proper member of the conv for session_end to work.
	# Add a ConversationMember if handle_fresh didn't add one (pre-binding doesn't add member).
	if not any(m.cli_session_id == pre_bound_session for m in conv.members_active.values()):
		from server.registry import ConversationMember
		member = ConversationMember(
			cli_session_id=pre_bound_session,
			sender="claude",
			cwd=pending["agents"][0]["project_path"],
			surface="windows",
			joined_at=0.0,
			alive=True,
		)
		conv.members_active[pre_bound_session] = member

	# Step 2: simulate SessionEnd — mark member dormant
	member = next(m for m in conv.members_active.values() if m.cli_session_id == pre_bound_session)
	member.alive = False
	member.session_ended_at = "2026-05-25T01:00:00+00:00"
	member.session_end_reason = "normal"
	# Unbind the session (as SessionEnd would do)
	registry.unbind_session(pre_bound_session)

	# Confirm member is dormant
	assert not member.alive
	assert pre_bound_session not in registry.session_to_conversation_id

	# Delete the first spawn pending file so we can detect new ones
	for f in _find_pending_files(tmp_path):
		f.unlink()

	# Step 3: resume
	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler2 = SpawnHandler(cfg, backend, logger, registry)
		await handler2.handle_resume({
			"type": "resume",
			"source_conversation_id": conv_id,
			"prompt": None,
			"issued_at": "2026-05-25T02:00:00Z",
		})

	# Step 4: verify continuation conv
	new_convs = [c for cid, c in registry.conversations.items() if cid != conv_id]
	assert len(new_convs) == 1, f"Expected 1 continuation conv, got: {[c.id for c in new_convs]}"
	new_conv = new_convs[0]
	assert new_conv.continued_from == conv_id, \
		f"Continuation conv must have continued_from={conv_id!r}; got {new_conv.continued_from!r}"

	# Pending file for the resume
	resume_pending_files = _find_pending_files(tmp_path)
	assert len(resume_pending_files) == 1
	resume_pending = json.loads(resume_pending_files[0].read_text(encoding="utf-8"))
	assert resume_pending["type"] == "resume"
	assert resume_pending["continued_from"] == conv_id
	assert len(resume_pending["agents"]) == 1

	# New session bound to new conv
	new_session = resume_pending["agents"][0]["cli_session_id"]
	assert registry.session_to_conversation_id.get(new_session) == new_conv.id


# ---------------------------------------------------------------------------
# Test 3: plain resume of fully-dormant single-member conv
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_resume_creates_continuation_conv(tmp_path):
	"""Plain resume of a fully-dormant single-member conv creates a new conv with
	continued_from set."""
	from server.spawn import SpawnHandler

	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = _make_config(tmp_path, spawn_root=spawn_root)
	backend = _make_backend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Build dormant source conv manually
	source = Conversation(id="conv-dormant-src", title="Dormant Task")
	m = ConversationMember(
		cli_session_id="sess-dormant-1",
		sender="claude-original",
		cwd=str(spawn_root / "myproject"),
		surface="windows",
		joined_at=0.0,
		alive=False,
	)
	source.members_active["sess-dormant-1"] = m
	registry.conversations["conv-dormant-src"] = source

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, logger, registry)
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-dormant-src",
			"prompt": "Continue the task",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	# New conv should exist with continued_from
	new_convs = [c for cid, c in registry.conversations.items() if cid != "conv-dormant-src"]
	assert len(new_convs) == 1
	new_conv = new_convs[0]
	assert new_conv.continued_from == "conv-dormant-src"

	# Source conv should be ended (all members resumed)
	assert source.state == "ended"

	# Pending file verifications
	pending = _read_pending(tmp_path)
	assert pending["type"] == "resume"
	assert pending["continued_from"] == "conv-dormant-src"
	assert len(pending["agents"]) == 1
	# handle_resume reuses the original cli_session_id from the dormant member
	assert pending["agents"][0]["cli_session_id"] == "sess-dormant-1"

	# Original session now bound to new conv
	assert registry.session_to_conversation_id.get("sess-dormant-1") == new_conv.id


# ---------------------------------------------------------------------------
# Test 4: fresh spawn with target_conversation_id joins existing conv
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_fresh_spawn_joins_existing_conversation(tmp_path):
	"""handle_fresh with target_conversation_id pre-binds session to the existing conv
	and does NOT mint a new conversation."""
	from server.spawn import SpawnHandler

	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = _make_config(tmp_path, spawn_root=spawn_root)
	backend = _make_backend()
	registry = Registry()
	logger = JsonlLogger(cfg.log_path)

	# Pre-create an active conversation (the target)
	existing_conv = Conversation(id="conv-existing-target", title="Existing Joint Task")
	m_existing = ConversationMember(
		cli_session_id="sess-host",
		sender="claude-host",
		cwd=str(spawn_root / "projects"),
		surface="windows",
		joined_at=0.0,
		alive=True,
	)
	existing_conv.members_active["sess-host"] = m_existing
	registry.conversations["conv-existing-target"] = existing_conv
	registry.bind_session("sess-host", "conv-existing-target")

	conv_count_before = len(registry.conversations)

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, logger, registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "projects",
			"prompt": "Join and help",
			"target_conversation_id": "conv-existing-target",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	# No new conversation minted
	assert len(registry.conversations) == conv_count_before, \
		"Fresh spawn joining existing conv must not mint a new one"

	pending = _read_pending(tmp_path)
	assert pending["conversation_id"] == "conv-existing-target"
	assert pending["agents"][0]["join_existing"] is True

	# Pre-bound session routes to the existing conv
	new_session = pending["agents"][0]["cli_session_id"]
	assert registry.session_to_conversation_id.get(new_session) == "conv-existing-target"
