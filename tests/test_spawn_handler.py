"""Tests for SpawnHandler argument parsing, rate limiting, and task scheduler launch."""

from __future__ import annotations

import asyncio
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
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="push-key-1")
	backend.write_conversation_member = AsyncMock()
	backend.remove_conversation_member = AsyncMock()
	backend.move_conversation_member = AsyncMock()
	backend.set_conversation_state = AsyncMock()
	backend.set_conversation_last_activity = AsyncMock()
	backend.set_session_home = AsyncMock()
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


@pytest.fixture(autouse=True)
def _assume_logged_in(monkeypatch):
	"""Default precondition: a user is logged in (no real quser call)."""
	from server.spawn import SpawnHandler
	monkeypatch.setattr(
		SpawnHandler, "_user_has_interactive_session", AsyncMock(return_value=True)
	)


@pytest.mark.asyncio
async def test_invoke_spawn_launcher_raises_on_failure_even_with_logger(tmp_path):
	"""B4: a launcher (schtasks) failure must propagate even when a logger is
	present, so the caller can surface it to the phone rather than treating the
	spawn as complete. It used to log-and-return when a logger was passed."""
	from server.spawn import invoke_spawn_launcher

	proc = AsyncMock()
	proc.returncode = 1
	proc.communicate = AsyncMock(return_value=(b"", b"ERROR: task not found"))
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
		with pytest.raises(Exception):
			await invoke_spawn_launcher(logger)


@pytest.mark.asyncio
async def test_handle_fresh_launcher_failure_notifies_phone(tmp_path):
	"""B4: when the launcher fails, handle_fresh must surface a phone-visible
	notice (like the quser gate) instead of swallowing it and leaving John in
	away mode with a phantom conversation and no signal."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock(side_effect=RuntimeError("schtasks exit 1"))):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		# Must not propagate the launcher error to the dispatcher.
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "myproject",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	backend.send_text.assert_awaited()
	notice = backend.send_text.await_args.args[0].lower()
	assert "spawn" in notice or "launch" in notice


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
	assert registry.conversations[pending["conversation_id"]].origin == "spawn"


@pytest.mark.parametrize("project", [
	"C:/Windows/System32",
	"C:\\Users\\JohnAnthony",
	"/etc/passwd",
	"\\\\server\\share",
	"../secrets",
	"foo/../../bar",
	"",
	"   ",
])
def test_validate_project_rejects_unsafe(project):
	from server.spawn import _validate_project
	assert _validate_project(project) is not None


@pytest.mark.parametrize("project", ["myproject", "rpdm/next-gen", "a/b/c"])
def test_validate_project_accepts_relative_segments(project):
	from server.spawn import _validate_project
	assert _validate_project(project) is None


@pytest.mark.asyncio
async def test_handle_fresh_rejects_unsafe_project(tmp_path):
	"""A phone-supplied absolute/traversal project must be rejected before any launch:
	no pending file, session never bound (path-traversal escape of the spawn root)."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()) as launcher:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_fresh({
			"type": "fresh",
			"surface": "windows",
			"project": "../../Users/JohnAnthony",
			"prompt": "exfil",
			"issued_at": "2026-05-25T00:00:00Z",
		})

	assert _find_pending_files(cfg) == []
	launcher.assert_not_called()
	assert registry.session_to_conversation_id == {}


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
async def test_handle_fresh_join_existing_persists_home_pointer(tmp_path):
	"""REV-113: the in-memory home pointer is set on both spawn branches, but the
	Firebase persist was gated on `not join_existing` - a spawn INTO an existing
	conversation set the home in memory only. After a restart, hydration found no
	persisted home and apply_fallback degraded to create_new, minting a stray
	"(home)" conversation. The persist must fire whenever home_newly_set, on both
	branches."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	# Pre-create an active conversation to join.
	existing_conv = Conversation(id="conv-existing", title="Existing")
	registry.conversations["conv-existing"] = existing_conv

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
		for _ in range(5):
			await asyncio.sleep(0)

	pending = _read_pending(cfg)
	sid = pending["agents"][0]["cli_session_id"]
	assert registry.session_home_conversation_id[sid] == "conv-existing"
	backend.set_session_home.assert_awaited_once_with(sid, "conv-existing")


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
	source.members_active["sess-1"] = m1
	source.members_active["sess-2"] = m2
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
	assert new_conv.origin == "resume"
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
	source.members_active["sess-alive"] = alive_m
	source.members_active["sess-dormant"] = dormant_m
	source.members_active["sess-perm"] = perm_lost_m
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
	source.members_active["sess-1"] = m
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
	source.members_active["sess-alive"] = alive_m
	source.members_active["sess-dormant"] = dormant_m
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
	assert "sess-alive" in source.members_active
	# Dormant member moved to new conv
	assert "sess-dormant" not in source.members_active


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
	source.members_active["sess-1"] = m1
	source.members_active["sess-2"] = m2
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
	for session_id in ("sess-1", "sess-2"):
		m = new_conv.members_active[session_id]
		assert m.alive is True, f"{m.sender} should be alive after resume"
		assert m.session_ended_at is None, f"{m.sender} dormancy ts should be cleared"
		assert m.session_end_reason is None, f"{m.sender} dormancy reason should be cleared"
		assert m.left_at is None, f"{m.sender} left_at should be cleared"

	# Regression check: alive-peer count from claude-2's perspective should
	# see claude-1 (the bug we're guarding against).
	alive_peers = [
		m for m in new_conv.members_active.values()
		if m.alive and m.cli_session_id != "sess-2"
	]
	assert len(alive_peers) == 1
	assert alive_peers[0].sender == "claude-1"


# ===========================================================================
# Chunk 4 Task 2: resume_session (board-driven session resume)
# ===========================================================================

def _seed_ended_session(registry: Registry, session_id: str, cwd: str, *, sender: str | None = None):
	"""Seed registry.sessions with an ended SessionRecord for resume_session tests."""
	from server.session_registry import SessionRegistry
	if registry.sessions is None:
		registry.sessions = SessionRegistry()
	registry.sessions.record_session_start(session_id, cwd=cwd)
	registry.sessions.record_session_end(session_id, reason="hook", ended_at="2026-07-07T00:00:00Z")
	if sender:
		registry.sessions.set_sender(session_id, sender)
	return registry.sessions.get(session_id)


@pytest.mark.asyncio
async def test_resume_session_standalone(tmp_path):
	"""Standalone resume_session (no target conversation) writes a pending file
	shaped for one agent, flips away mode, and notes the resume sentinel."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()
	assert registry.global_away_mode is False

	cwd = str(spawn_root / "myproject")
	_seed_ended_session(registry, "sess-1", cwd, sender="claude-1")

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-1",
			"issued_at": "2026-07-07T00:00:00Z",
		})

	pending = _read_pending(cfg)
	assert pending["type"] == "resume_session"
	assert len(pending["agents"]) == 1
	agent = pending["agents"][0]
	assert agent["surface"] == "windows"
	assert agent["cli_session_id"] == "sess-1"
	assert agent["project_path"] == cwd
	assert agent["prior_sender"] == "claude-1"
	assert "ask_human" in agent["prompt"]
	assert "join_conversation" not in agent["prompt"]

	assert registry.global_away_mode is True
	# Sentinel noted: a dummy id with the matching cwd resolves back to the real
	# session id recorded by note_spawn_resume.
	assert registry.sessions.check_resume_id_change("x", cwd) == "sess-1"


@pytest.mark.asyncio
async def test_resume_session_into_conversation(tmp_path):
	"""Resuming into an existing active conversation with an alive peer pre-adds
	the member under the same session id, appends a system message, and the
	prompt directs the agent to join_conversation."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	target = Conversation(id="conv-target", title="Target")
	alive_m = ConversationMember(
		cli_session_id="sess-alive", sender="alive-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=True,
	)
	target.members_active["sess-alive"] = alive_m
	registry.conversations["conv-target"] = target

	cwd = str(spawn_root / "myproject")
	_seed_ended_session(registry, "sess-1", cwd, sender="claude-1")

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-1",
			"target_conversation_id": "conv-target",
			"issued_at": "2026-07-07T00:00:00Z",
		})

	assert "sess-1" in target.members_active
	member = target.members_active["sess-1"]
	assert member.sender == "claude-1"
	assert registry.session_to_conversation_id["sess-1"] == "conv-target"

	system_msgs = [m for m in target.messages if m.get("type") == "system"]
	assert any("John resumed claude-1 into this conversation." in m["text"] for m in system_msgs)

	pending = _read_pending(cfg)
	agent = pending["agents"][0]
	assert "join_conversation(sender=" in agent["prompt"]
	assert "conv-target" in agent["prompt"]


@pytest.mark.asyncio
async def test_resume_session_into_empty_conversation_prompt(tmp_path):
	"""When the target conversation's only other member is dormant, other_alive
	is 0 and the solo-rule prompt (ask_human/notify_human, no join_conversation)
	applies even though the resumed member is still added to the roster."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	target = Conversation(id="conv-target", title="Target")
	dormant_m = ConversationMember(
		cli_session_id="sess-dormant", sender="dormant-agent", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	target.members_active["sess-dormant"] = dormant_m
	registry.conversations["conv-target"] = target

	cwd = str(spawn_root / "myproject")
	_seed_ended_session(registry, "sess-1", cwd, sender="claude-1")

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-1",
			"target_conversation_id": "conv-target",
			"issued_at": "2026-07-07T00:00:00Z",
		})

	assert "sess-1" in target.members_active
	pending = _read_pending(cfg)
	agent = pending["agents"][0]
	assert "ask_human" in agent["prompt"]
	assert "notify_human" in agent["prompt"]
	assert "join_conversation" not in agent["prompt"]


@pytest.mark.asyncio
async def test_resume_session_rejects_live_record(tmp_path):
	"""A registry record that is still live (not ended/lost) is rejected: no
	pending file, no launch, and a phone-visible 'still live' notice."""
	from server.spawn import SpawnHandler
	from server.session_registry import SessionRegistry
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()
	registry.sessions = SessionRegistry()
	registry.sessions.record_session_start("sess-1", cwd=str(spawn_root / "myproject"))
	registry.sessions.upsert_from_hook("sess-1", state="active")

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()) as launcher:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-1",
			"issued_at": "2026-07-07T00:00:00Z",
		})

	assert _find_pending_files(cfg) == []
	launcher.assert_not_called()
	backend.send_text.assert_awaited()
	assert "still live" in backend.send_text.await_args.args[0]


@pytest.mark.asyncio
async def test_resume_session_rejects_missing_or_cwdless(tmp_path):
	"""An unknown session id, and a known-but-cwdless ended record, are both
	rejected before any pending file is written."""
	from server.spawn import SpawnHandler
	from server.session_registry import SessionRegistry
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()
	registry.sessions = SessionRegistry()

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()) as launcher:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

		# Unknown session id.
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-unknown",
			"issued_at": "2026-07-07T00:00:00Z",
		})
		assert _find_pending_files(cfg) == []
		assert "not found" in backend.send_text.await_args.args[0]

		# Known, ended record whose cwd got cleared out from under it.
		_seed_ended_session(registry, "sess-1", str(spawn_root / "myproject"), sender="claude-1")
		registry.sessions.get("sess-1").cwd = ""
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-1",
			"issued_at": "2026-07-07T00:00:00Z",
		})
		assert _find_pending_files(cfg) == []
		assert "no working directory" in backend.send_text.await_args.args[0]

	launcher.assert_not_called()


@pytest.mark.asyncio
async def test_resume_session_invalid_target(tmp_path):
	"""An unknown target_conversation_id is rejected before away mode is touched -
	the auto-enable flip happens only after all validation passes."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()
	assert registry.global_away_mode is False

	cwd = str(spawn_root / "myproject")
	_seed_ended_session(registry, "sess-1", cwd, sender="claude-1")

	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()) as launcher:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume_session({
			"type": "resume_session",
			"session_id": "sess-1",
			"target_conversation_id": "conv-nope",
			"issued_at": "2026-07-07T00:00:00Z",
		})

	assert _find_pending_files(cfg) == []
	launcher.assert_not_called()
	backend.send_text.assert_awaited()
	assert registry.global_away_mode is False


@pytest.mark.asyncio
async def test_cancel_prior_pending_spares_live_session_cancels_dead(tmp_path):
	"""_cancel_prior_pending (spawn's stale-pending cleanup) must cancel a pending
	owned by a dead/unknown session while sparing one owned by a live member -
	spawning into a conversation must not destroy a live peer's in-flight
	question. Regression for behavior formerly unit-tested directly on
	Registry.cancel_stale_pending_for_conversation (deleted); the filter now lives
	inline in _cancel_prior_pending, routed through terminate_pending."""
	from server.spawn import SpawnHandler
	from server.registry import Conversation, ConversationMember
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock()
	registry = Registry()

	conv = Conversation(id="conv-1", title="Conv")
	live_m = ConversationMember(
		cli_session_id="s-live", sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=True,
	)
	conv.members_active["s-live"] = live_m
	registry.conversations["conv-1"] = conv

	live_fut = registry.add("conv-1", "s-live", "Claude", "req-live")
	dead_fut = registry.add("conv-1", "s-dead", "Sparkles", "req-dead")

	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	await handler._cancel_prior_pending("conv-1")

	assert not live_fut.cancelled()
	assert dead_fut.cancelled()
	assert registry.find_by_request_id("conv-1", "req-live") is not None
	assert registry.find_by_request_id("conv-1", "req-dead") is None
	backend.mark_question_cancelled.assert_awaited_once_with("conv-1", "req-dead")


@pytest.mark.asyncio
async def test_launch_resume_agent_no_login(tmp_path):
	"""launch_resume_agent returns False and writes no pending file when no one is
	logged in to the desktop - the quser gate applies to the raw launch primitive
	directly (Task 3's convene fork-arm calls it without going through
	handle_resume_session)."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	with patch.object(SpawnHandler, "_user_has_interactive_session", new=AsyncMock(return_value=False)):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		ok = await handler.launch_resume_agent(
			session_id="sess-1", surface="windows", cwd=str(spawn_root / "myproject"),
			prompt="hello", prior_sender="claude-1",
		)

	assert ok is False
	assert _find_pending_files(cfg) == []


@pytest.mark.asyncio
async def test_launch_resume_agent_pending_file_write_failure_returns_false(tmp_path):
	"""launch_resume_agent's contract is returns-bool-never-raises: a pending-file
	write failure (AV-locked logs dir) must surface as resume_session_launch_failed
	plus False, not raise through to convene's loop (double-launch-on-replay risk)."""
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	cfg = make_config_with_wsl(tmp_path, spawn_root=spawn_root)
	backend = make_backend()
	registry = Registry()

	with patch.object(SpawnHandler, "_user_has_interactive_session", new=AsyncMock(return_value=True)), \
		patch.object(SpawnHandler, "_write_pending_file", side_effect=OSError("locked")):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		ok = await handler.launch_resume_agent(
			session_id="s-x", surface="windows", cwd=str(spawn_root / "myproject"),
			prompt="p", prior_sender=None,
		)

	assert ok is False
	assert _find_pending_files(cfg) == []
	log_contents = Path(cfg.log_path).read_text(encoding="utf-8")
	assert "resume_session_launch_failed" in log_contents


def _registry_with_dormant_agy(session_id: str = "agy-sess-1") -> Registry:
	import time as _time
	from server.registry import Conversation, ConversationMember
	from server.session_registry import SessionRegistry
	registry = Registry()
	conv = Conversation(id="conv-src", title="Source")
	conv.members_active[session_id] = ConversationMember(
		cli_session_id=session_id, sender="Sparkles", cwd="C:/Work/X",
		surface="windows", joined_at=_time.time(), alive=False,
	)
	registry.conversations["conv-src"] = conv
	registry.sessions = SessionRegistry()
	registry.sessions.record_session_start(session_id, cwd="C:/Work/X", cli="antigravity")
	return registry


@pytest.mark.asyncio
async def test_handle_resume_skips_antigravity_member_with_notice(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config_with_wsl(tmp_path)
	backend = make_backend()
	registry = _registry_with_dormant_agy()
	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock()) as launcher:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({"type": "resume", "source_conversation_id": "conv-src"})
	assert _find_pending_files(cfg) == []
	launcher.assert_not_awaited()
	backend.send_text.assert_awaited()
	notices = " ".join(str(c.args[0]) for c in backend.send_text.await_args_list)
	assert "agy --conversation agy-sess-1" in notices


@pytest.mark.asyncio
async def test_handle_resume_session_rejects_antigravity(tmp_path):
	from server.spawn import SpawnHandler
	from server.session_registry import SessionRegistry
	cfg = make_config_with_wsl(tmp_path)
	backend = make_backend()
	registry = Registry()
	registry.sessions = SessionRegistry()
	registry.sessions.record_session_start("agy-sess-2", cwd="C:/Work/X", cli="antigravity")
	registry.sessions.record_session_end("agy-sess-2", reason="exit", ended_at="2026-07-14T00:00:00Z")
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	await handler.handle_resume_session({"session_id": "agy-sess-2"})
	assert _find_pending_files(cfg) == []
	backend.send_text.assert_awaited()
	assert "agy --conversation agy-sess-2" in backend.send_text.await_args.args[0]


@pytest.mark.asyncio
async def test_launch_resume_agent_returns_false_for_antigravity(tmp_path):
	from server.spawn import SpawnHandler
	from server.session_registry import SessionRegistry
	cfg = make_config_with_wsl(tmp_path)
	backend = make_backend()
	registry = Registry()
	registry.sessions = SessionRegistry()
	registry.sessions.record_session_start("agy-sess-3", cwd="C:/Work/X", cli="antigravity")
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	ok = await handler.launch_resume_agent(
		session_id="agy-sess-3", surface="windows", cwd="C:/Work/X", prompt="p", prior_sender=None,
	)
	assert ok is False
	assert _find_pending_files(cfg) == []
	assert "agy --conversation agy-sess-3" in backend.send_text.await_args.args[0]


# ===========================================================================
# Rollback-to-dormant on launcher failure
# ===========================================================================

def _registry_with_dormant_claude(session_id: str) -> Registry:
	import time as _time
	from server.registry import Conversation, ConversationMember
	registry = Registry()
	conv = Conversation(id="conv-src", title="Source")
	conv.members_active[session_id] = ConversationMember(
		cli_session_id=session_id, sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=_time.time(), alive=False,
	)
	registry.conversations["conv-src"] = conv
	return registry


def _registry_with_target_conversation(conv_id: str) -> Registry:
	from server.registry import Conversation
	registry = Registry()
	registry.conversations[conv_id] = Conversation(id=conv_id, title="Target")
	return registry


@pytest.mark.asyncio
async def test_handle_resume_launcher_failure_rolls_members_back(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config_with_wsl(tmp_path)
	backend = make_backend()
	registry = _registry_with_dormant_claude("sess-d1")  # helper: dormant member, no agy session record
	with patch.object(SpawnHandler, "_invoke_launcher", new=AsyncMock(side_effect=RuntimeError("schtasks 1"))):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle_resume({"type": "resume", "source_conversation_id": "conv-src"})
	# The continuation conversation exists; its member is rolled back dormant + unbound.
	new_convs = [c for cid, c in registry.conversations.items() if cid != "conv-src"]
	assert len(new_convs) == 1
	member = new_convs[0].members_active["sess-d1"]
	assert member.alive is False
	assert registry.session_to_conversation_id.get("sess-d1") is None
	notices = " ".join(str(c.args[0]) for c in backend.send_text.await_args_list)
	assert "long-press" in notices


@pytest.mark.asyncio
async def test_resume_session_launch_failure_reverts_added_member(tmp_path):
	from server.spawn import SpawnHandler
	from server.session_registry import SessionRegistry
	cfg = make_config_with_wsl(tmp_path)
	backend = make_backend()
	registry = _registry_with_target_conversation("conv-tgt")  # helper: active conv, no member for the session
	registry.sessions = SessionRegistry()
	registry.sessions.record_session_start("sess-r1", cwd="C:/Work/X")
	registry.sessions.record_session_end("sess-r1", reason="exit", ended_at="2026-07-15T00:00:00Z")
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	with patch.object(SpawnHandler, "launch_resume_agent", new=AsyncMock(return_value=False)):
		await handler.handle_resume_session({"session_id": "sess-r1", "target_conversation_id": "conv-tgt"})
	member = registry.conversations["conv-tgt"].members_active.get("sess-r1")
	assert member is not None and member.alive is False  # added member reverted to dormant, kept for visibility
	assert member.session_end_reason == "launch-failed"
	assert registry.session_to_conversation_id.get("sess-r1") is None
