"""P5-1 (F-19/F-81): the new MCP conversation handlers must log a success
event so a combine/open/enter/leave round-trip is reconstructable from
switchboard.jsonl. Asserted by reading the JSONL back and filtering on
event == "info"."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


def _info_details(log_path: str) -> list[str]:
	events = [json.loads(line) for line in Path(log_path).read_text().splitlines() if line]
	return [e["detail"] for e in events if e.get("event") == "info"]


@pytest.mark.asyncio
async def test_leave_conversation_logs_success(tmp_path: Path):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-l1", member_session_id="s-l1", sender="Claude")
	registry.conversations["conv-l1"] = conv
	registry.bind_session("s-l1", "conv-l1")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.leave_conversation("Claude", "bye", cli_session_id="s-l1", cwd="C:/Work/X")

	assert json.loads(result) == {"status": "ok", "conversation_id": "conv-l1"}
	assert any("leave_conversation" in d and "conv-l1" in d for d in _info_details(cfg.log_path)), \
		"leave_conversation must log a success info event naming the conversation"


@pytest.mark.asyncio
async def test_set_away_mode_logs_success(tmp_path: Path):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))
	registry = Registry()
	registry.global_away_mode = True
	conv = make_active_conversation(conversation_id="conv-a1", member_session_id="s-a1", sender="Claude")
	registry.conversations["conv-a1"] = conv
	registry.bind_session("s-a1", "conv-a1")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	await handlers.set_away_mode(False, cli_session_id="s-a1", cwd="C:/Work/X")

	assert any("set_away_mode" in d for d in _info_details(cfg.log_path)), \
		"set_away_mode must log a success info event"


@pytest.mark.asyncio
async def test_lookup_conversation_ids_logs_success(tmp_path: Path):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-k1", member_session_id="s-k1", sender="Claude")
	registry.conversations["conv-k1"] = conv
	registry.bind_session("s-k1", "conv-k1")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))

	await handlers.lookup_conversation_ids(title_contains="test", cli_session_id="s-k1", cwd="C:/Work/X")

	assert any("lookup_conversation_ids" in d for d in _info_details(cfg.log_path)), \
		"lookup_conversation_ids must log a success info event"
