"""P1-8 (M09): set_away_mode(false) must resolve pending ask_human questions
with the at-desk notice (decided 2026-06-11: resolve-with-sentinel, unified
on _apply_bulk_respond_decision), so blocked askers wake in their own
terminals instead of blocking until the 24h timeout."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend

AT_DESK_NOTICE = "John is back at his desk; your question was not answered remotely. Re-ask in the terminal."


def _handlers(tmp_path: Path, registry: Registry, backend: RecordingBackend):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)
	return build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))


@pytest.mark.asyncio
async def test_tool_side_away_exit_resolves_pendings_with_notice(tmp_path):
	registry = Registry()
	registry.global_away_mode = True
	conv = make_active_conversation(conversation_id="conv-x1", member_session_id="s-x1", sender="Claude")
	registry.conversations["conv-x1"] = conv
	registry.bind_session("s-x1", "conv-x1")
	backend = RecordingBackend()
	handlers = _handlers(tmp_path, registry, backend)

	future = registry.add("conv-x1", "s-x1", "Claude", request_id="req-1", msg_id="msg-1")

	result = await handlers.set_away_mode(False, cli_session_id="s-x1", cwd="C:/Work/X")

	assert registry.global_away_mode is False
	assert future.done() and not future.cancelled()
	assert future.result() == AT_DESK_NOTICE
	assert registry.pending_count == 0
	assert "1 pending" in result, f"return should report the resolution count, got: {result!r}"


@pytest.mark.asyncio
async def test_tool_side_away_exit_with_no_pendings_is_plain(tmp_path):
	registry = Registry()
	registry.global_away_mode = True
	conv = make_active_conversation(conversation_id="conv-x2", member_session_id="s-x2", sender="Claude")
	registry.conversations["conv-x2"] = conv
	registry.bind_session("s-x2", "conv-x2")
	backend = RecordingBackend()
	handlers = _handlers(tmp_path, registry, backend)

	result = await handlers.set_away_mode(False, cli_session_id="s-x2", cwd="C:/Work/X")
	assert result == "ok. away_mode=False"
	assert registry.global_away_mode is False
