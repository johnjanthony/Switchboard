"""Tests for SpawnHandler argument parsing, rate limiting, and task scheduler launch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry


def make_backend() -> MagicMock:
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
	backend.remove_session_binding = AsyncMock()
	backend.set_global_away_mode = AsyncMock()
	return backend






# ===========================================================================
# Tasks 25 & 26: handle_fresh / handle_resume
# ===========================================================================

def make_config_with_wsl(tmp_path: Path, spawn_root=None, wsl_home: str = "/home/john") -> Config:
	"""Config with both windows_spawn_root and wsl_home_resolved set."""
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=spawn_root,
		wsl_home_resolved=wsl_home,
		wsl_spawn_root_segment="work",
	)


def _find_pending_files(cfg: Config) -> list[Path]:
	return list(Path(cfg.log_path).parent.glob("spawn-pending-*.json"))


def _read_pending(cfg: Config) -> dict:
	files = _find_pending_files(cfg)
	assert len(files) == 1, f"Expected 1 pending file, found {len(files)}: {files}"
	return json.loads(files[0].read_text())


@pytest.mark.asyncio
async def test_handle_fresh_windows_writes_pending_file(tmp_path):
	"""handle_fresh with surface=windows writes spawn-pending file with correct fields
	and pre-binds the cli_session_id in the registry."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "myproject",
			"prompt": "Do stuff",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	pending = _read_pending(cfg)
	assert pending["type"] == "fresh"
	assert len(pending["agents"]) == 1
	agent = pending["agents"][0]
	assert agent["surface"] == "windows"
	assert str(spawn_root / "myproject") == agent["project_path"]
	# session must be pre-bound in the registry
	session_id = agent["cli_session_id"]
	assert session_id in registry.session_to_conversation_id
	assert pending["conversation_id"] == registry.session_to_conversation_id[session_id]


@pytest.mark.asyncio
async def test_handle_fresh_wsl_uses_wsl_home_path(tmp_path):
	"""handle_fresh with surface=wsl builds project_path from wsl_home_resolved and segment."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root, wsl_home="/home/jane")
	backend = make_backend()
	registry = Registry()

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "wsl",
			"project": "rpdm/next-gen",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	pending = _read_pending(cfg)
	agent = pending["agents"][0]
	assert agent["surface"] == "wsl"
	assert agent["project_path"] == "/home/jane/work/rpdm/next-gen"


@pytest.mark.asyncio
async def test_handle_fresh_target_conversation_joins_existing(tmp_path):
	"""handle_fresh with target_conversation_id set binds session to the existing conv,
	does not mint a new one."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	# Pre-create an active conversation
	existing_conv = Conversation(id="conv-existing", title="Existing")
	registry.conversations["conv-existing"] = existing_conv

	conv_count_before = len(registry.conversations)

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "myproject",
			"prompt": "Join and help",
			"target_conversation_id": "conv-existing",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	# No new conversation created
	assert len(registry.conversations) == conv_count_before
	pending = _read_pending(cfg)
	assert pending["conversation_id"] == "conv-existing"
	assert pending["agents"][0]["join_existing"] is True
	# session bound to existing conv
	session_id = pending["agents"][0]["cli_session_id"]
	assert registry.session_to_conversation_id[session_id] == "conv-existing"


@pytest.mark.asyncio
async def test_handle_fresh_auto_enables_away_mode(tmp_path):
	"""handle_fresh sets registry.global_away_mode to True when it was False."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()
	assert registry.global_away_mode is False

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "myproject",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	assert registry.global_away_mode is True


@pytest.mark.asyncio
async def test_handle_resume_creates_continuation(tmp_path):
	"""handle_resume with 2 dormant members creates a new conv with continued_from set,
	both sessions pre-bound, and a pending file containing both agents."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	# Build source conv with 2 dormant members
	source = Conversation(id="conv-src", title="Source")
	m1 = ConversationMember(
		cli_session_id="sess-1", sender="claude-1", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	m2 = ConversationMember(
		cli_session_id="sess-2", sender="claude-2", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	source.members_active["claude-1"] = m1
	source.members_active["claude-2"] = m2
	registry.conversations["conv-src"] = source

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	# One new conv minted
	new_convs = [c for cid, c in registry.conversations.items() if cid != "conv-src"]
	assert len(new_convs) == 1
	new_conv = new_convs[0]
	assert new_conv.continued_from == "conv-src"
	# Both sessions bound to new conv
	assert registry.session_to_conversation_id["sess-1"] == new_conv.id
	assert registry.session_to_conversation_id["sess-2"] == new_conv.id
	# Pending file has both agents
	pending = _read_pending(cfg)
	assert pending["type"] == "resume"
	assert pending["continued_from"] == "conv-src"
	assert len(pending["agents"]) == 2


@pytest.mark.asyncio
async def test_handle_resume_skips_alive_and_permanently_lost(tmp_path):
	"""handle_resume only picks up dormant, non-permanently-lost, unbound members."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	source = Conversation(id="conv-src", title="Source")
	alive_m = ConversationMember(
		cli_session_id="sess-alive", sender="alive-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=True,
	)
	dormant_m = ConversationMember(
		cli_session_id="sess-dormant", sender="dormant-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	perm_lost_m = ConversationMember(
		cli_session_id="sess-perm", sender="perm-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False, session_lost_permanently=True,
	)
	source.members_active["alive-agent"] = alive_m
	source.members_active["dormant-agent"] = dormant_m
	source.members_active["perm-agent"] = perm_lost_m
	registry.conversations["conv-src"] = source

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	pending = _read_pending(cfg)
	assert len(pending["agents"]) == 1
	assert pending["agents"][0]["cli_session_id"] == "sess-dormant"


@pytest.mark.asyncio
async def test_handle_resume_ends_source_when_all_resumed(tmp_path):
	"""When all members are resumable, the source conv transitions to 'ended'."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	source = Conversation(id="conv-src", title="Source")
	m = ConversationMember(
		cli_session_id="sess-1", sender="agent-1", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	source.members_active["agent-1"] = m
	registry.conversations["conv-src"] = source

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	assert source.state == "ended"
	assert source.ended_at is not None


@pytest.mark.asyncio
async def test_handle_resume_keeps_source_active_with_remaining_alive(tmp_path):
	"""When some members remain alive, the source conv stays active."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	source = Conversation(id="conv-src", title="Source")
	alive_m = ConversationMember(
		cli_session_id="sess-alive", sender="alive-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=True,
	)
	dormant_m = ConversationMember(
		cli_session_id="sess-dormant", sender="dormant-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	source.members_active["alive-agent"] = alive_m
	source.members_active["dormant-agent"] = dormant_m
	registry.conversations["conv-src"] = source

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	# Alive member still in source
	assert source.state == "active"
	assert "alive-agent" in source.members_active
	# Dormant member moved to new conv
	assert "dormant-agent" not in source.members_active


@pytest.mark.asyncio
async def test_handle_resume_flips_members_to_alive(tmp_path):
	"""Resumed members must have alive=True so message_and_await_agent's
	alive-peer count includes them. Without this, a two-agent resume yields
	__CONVERSATION_EMPTY__ for both agents on their first speak attempt."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	source = Conversation(id="conv-src", title="Source")
	m1 = ConversationMember(
		cli_session_id="sess-1", sender="claude-1", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
		session_ended_at=123.0, session_end_reason="hook",
	)
	m2 = ConversationMember(
		cli_session_id="sess-2", sender="claude-2", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
		session_ended_at=124.0, session_end_reason="hook",
	)
	source.members_active["claude-1"] = m1
	source.members_active["claude-2"] = m2
	registry.conversations["conv-src"] = source

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"prompt": None,
			"issued_at": "2026-05-25T00:00:00Z",
		})

	new_convs = [c for cid, c in registry.conversations.items() if cid != "conv-src"]
	assert len(new_convs) == 1
	new_conv = new_convs[0]
	# Both resumed members must be alive=True with dormancy fields cleared
	for sender in ("claude-1", "claude-2"):
		m = new_conv.members_active[sender]
		assert m.alive is True, f"{sender} should be alive after resume"
		assert m.session_ended_at is None, f"{sender} dormancy ts should be cleared"
		assert m.session_end_reason is None, f"{sender} dormancy reason should be cleared"
		assert m.left_at is None, f"{sender} left_at should be cleared"

	# Regression check: alive-peer count from claude-2's perspective should
	# see claude-1 (the bug we're guarding against).
	alive_peers = [
		m for m in new_conv.members_active.values()
		if m.alive and m.cli_session_id != "sess-2"
	]
	assert len(alive_peers) == 1
	assert alive_peers[0].sender == "claude-1"
