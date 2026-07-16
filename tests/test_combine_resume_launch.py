"""P0-1 acceptance: combine actually resumes dormant members.

Covers the desktop-session gate for dormant moves (P0-4), the bind+alive flip
at relaunch time (P0-2 invariant alignment), launcher invocation from both
trigger paths (MCP tool + phone dispatch), and pending_dir threading on the
phone path (H14)."""

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
from tests.conftest import _make_loop_supervisor
from tests.test_gateway_notify_human import RecordingBackend


def _cfg(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)


def _member(session_id: str, sender: str, alive: bool = True) -> ConversationMember:
	return ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd="C:/Work/X",
		surface="windows",
		joined_at=time.time(),
		alive=alive,
	)


def _registry_with_source_and_target() -> Registry:
	"""Source conv-src holds one dormant member; target conv-tgt holds the
	alive combiner."""
	registry = Registry()
	source = Conversation(id="conv-src", title="Source")
	source.members_active["sess-dormant"] = _member("sess-dormant", "Dormant", alive=False)
	registry.conversations["conv-src"] = source
	target = Conversation(id="conv-tgt", title="Target")
	target.members_active["sess-combiner"] = _member("sess-combiner", "Combiner", alive=True)
	registry.conversations["conv-tgt"] = target
	registry.bind_session("sess-combiner", "conv-tgt")
	return registry


@pytest.mark.asyncio
async def test_combine_with_dormant_member_aborts_without_desktop_session(tmp_path):
	"""P0-4 applied to combine: a dormant move means a relaunch; with no
	desktop session the whole combine aborts (no partial move, no strand)."""
	from server.conversation_ops import _perform_combine
	registry = _registry_with_source_and_target()
	backend = RecordingBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=False)):
		result = await _perform_combine(
			registry, "conv-src", "conv-tgt", logger, pending_dir=tmp_path, backend=backend,
		)

	assert result.startswith("ERROR"), f"expected abort, got: {result}"
	assert "logged in" in result
	# Nothing moved, nothing ended, nothing written
	source = registry.conversations["conv-src"]
	target = registry.conversations["conv-tgt"]
	assert "sess-dormant" in source.members_active
	assert source.state == "active"
	assert "sess-dormant" not in target.members_active
	assert "sess-dormant" not in registry.session_to_conversation_id
	assert list(tmp_path.glob("spawn-pending-*.json")) == []


@pytest.mark.asyncio
async def test_combine_dormant_member_binds_flips_alive_and_fires_launcher(tmp_path):
	"""H08/H09: the pending file alone does nothing; the launcher must fire.
	And the bind must come WITH alive=True (P0-2 invariant: bound = alive or
	relaunch-in-flight-with-alive-set), clearing the dormancy fields."""
	from server.conversation_ops import _perform_combine
	registry = _registry_with_source_and_target()
	dormant = registry.conversations["conv-src"].members_active["sess-dormant"]
	dormant.session_ended_at = "2026-06-11T00:00:00+00:00"
	dormant.session_end_reason = "logout"
	backend = RecordingBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
			patch("server.spawn.invoke_spawn_launcher", AsyncMock()) as mock_launch:
		result = await _perform_combine(
			registry, "conv-src", "conv-tgt", logger, pending_dir=tmp_path, backend=backend,
		)

	assert result.startswith("ok"), f"unexpected: {result}"
	target = registry.conversations["conv-tgt"]
	member = target.members_active["sess-dormant"]
	# bind + alive together at relaunch time
	assert member.alive is True
	assert member.session_ended_at is None
	assert member.session_end_reason is None
	assert registry.session_to_conversation_id.get("sess-dormant") == "conv-tgt"
	# pending file written AND launcher actually fired
	files = list(tmp_path.glob("spawn-pending-*.json"))
	assert len(files) == 1
	payload = json.loads(files[0].read_text(encoding="utf-8"))
	assert payload["type"] == "combine_resume"
	assert payload["agents"][0]["cli_session_id"] == "sess-dormant"
	mock_launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_combine_path_fires_launcher_for_dormant_member(tmp_path):
	"""P0-1 acceptance, MCP trigger path: the combine_conversations tool
	threads a real pending_dir and the launcher fires."""
	cfg = _cfg(tmp_path)
	registry = _registry_with_source_and_target()
	backend = RecordingBackend()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
			patch("server.spawn.invoke_spawn_launcher", AsyncMock()) as mock_launch:
		result = await handlers.combine_conversations(
			"conv-src", "conv-tgt",
			cli_session_id="sess-combiner", cwd="C:/Work/X",
		)

	assert json.loads(result)["status"] == "ok", f"unexpected: {result}"
	assert len(list(tmp_path.glob("spawn-pending-*.json"))) == 1
	mock_launch.assert_awaited_once()


class _CapturingCombineBackend(RecordingBackend):
	def __init__(self) -> None:
		super().__init__()
		self.combine_handler = None

	async def start_combine_command_listener(self, handler):
		self.combine_handler = handler


@pytest.mark.asyncio
async def test_phone_combine_path_passes_pending_dir_and_fires_launcher(tmp_path):
	"""H14: the phone dispatch path used to pass pending_dir=None, so no
	combine_resume file was ever written and no launcher could fire. The
	dispatcher must thread a real pending_dir through to _perform_combine."""
	from server.gateway.dispatch import dispatch_combine_commands
	registry = _registry_with_source_and_target()
	backend = _CapturingCombineBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_loop_supervisor(backend, logger, "dispatch_combine_commands")

	await dispatch_combine_commands(registry, backend, logger, supervisor, pending_dir=tmp_path)
	assert backend.combine_handler is not None

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
			patch("server.spawn.invoke_spawn_launcher", AsyncMock()) as mock_launch:
		await backend.combine_handler({
			"source_conversation_id": "conv-src",
			"target_conversation_id": "conv-tgt",
			"issued_at": "2026-06-11T00:00:00Z",
		})

	assert len(list(tmp_path.glob("spawn-pending-*.json"))) == 1
	mock_launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_combine_launcher_failure_rolls_dormant_member_back(tmp_path):
	from server.conversation_ops import _perform_combine
	registry = _registry_with_source_and_target()  # has a dormant movable member "sess-dormant"
	backend = RecordingBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	member_before = registry.conversations["conv-src"].members_active["sess-dormant"]
	saved_reason = member_before.session_end_reason

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
		patch("server.spawn.invoke_spawn_launcher", AsyncMock(side_effect=RuntimeError("schtasks 1"))):
		result = await _perform_combine(
			registry, "conv-src", "conv-tgt", logger, pending_dir=tmp_path, backend=backend,
		)
	for _ in range(5):
		await asyncio.sleep(0)

	assert not result.startswith("ERROR")  # the combine itself committed
	member = registry.conversations["conv-tgt"].members_active["sess-dormant"]
	assert member.alive is False
	assert member.session_end_reason == saved_reason  # captured fields restored
	assert registry.session_to_conversation_id.get("sess-dormant") is None  # unbound
	assert any(cid == "conv-tgt" and m.alive is False for cid, m in backend.member_writes)  # dormant re-persisted
	assert any("long-press" in t for t in backend.sent_texts)  # working recovery advertised


@pytest.mark.asyncio
async def test_combine_launcher_success_keeps_flip(tmp_path):
	from server.conversation_ops import _perform_combine
	registry = _registry_with_source_and_target()
	backend = RecordingBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)), \
		patch("server.spawn.invoke_spawn_launcher", AsyncMock(return_value=None)):
		result = await _perform_combine(
			registry, "conv-src", "conv-tgt", logger, pending_dir=tmp_path, backend=backend,
		)
	assert not result.startswith("ERROR")
	member = registry.conversations["conv-tgt"].members_active["sess-dormant"]
	assert member.alive is True
	assert registry.session_to_conversation_id.get("sess-dormant") == "conv-tgt"


@pytest.mark.asyncio
async def test_combine_leaves_antigravity_member_dormant_with_notice(tmp_path):
	from server.conversation_ops import _perform_combine
	from server.session_registry import SessionRegistry
	registry = _registry_with_source_and_target()
	registry.sessions = SessionRegistry()
	registry.sessions.record_session_start("sess-dormant", cwd="C:/Work/X", cli="antigravity")
	backend = RecordingBackend()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	with patch("server.spawn.user_has_interactive_session", AsyncMock(return_value=True)):
		result = await _perform_combine(
			registry, "conv-src", "conv-tgt", logger, pending_dir=tmp_path, backend=backend,
		)
	for _ in range(5):
		await asyncio.sleep(0)

	assert not result.startswith("ERROR")
	member = registry.conversations["conv-tgt"].members_active["sess-dormant"]
	assert member.alive is False
	assert registry.session_to_conversation_id.get("sess-dormant") is None
	assert not list(tmp_path.glob("spawn-pending-*.json"))
	assert any("agy --conversation sess-dormant" in t for t in backend.sent_texts)
