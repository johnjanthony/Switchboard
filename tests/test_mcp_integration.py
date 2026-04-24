"""End-to-end integration test for the FastMCP tool wiring in server/main.py.

Guards the decorator contract between `_build_fastmcp` and `mcp[cli]`. If
`mcp[cli]` changes its `@mcp.tool()` semantics (parameter names, async
handling, return-value envelope), this test catches it — instead of the
failure waiting until manual smoke-test time.
"""

from __future__ import annotations

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.main import _build_fastmcp
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.mark.asyncio
async def test_mcp_notify_human_tool_is_registered_and_invocable(cfg):
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	tools = await mcp.list_tools()
	tool_names = {t.name for t in tools}
	assert tool_names == {
		"ask_human",
		"notify_human",
		"send_document_human",
		"message_and_await_agent",
		"enter_away_mode",
		"exit_away_mode",
	}

	content, structured = await mcp.call_tool(
		"notify_human",
		{"message": "hello world", "channel_id": "ir2-20260422-143052"},
	)

	assert structured == {"result": "ok"}
	assert any(getattr(block, "text", "") == "ok" for block in content)
	assert backend.sent_notifications == [("Claude", "hello world")]


@pytest.mark.asyncio
async def test_mcp_enter_away_mode_tool_flips_registry(cfg, tmp_path):
	logger = JsonlLogger(cfg.log_path)
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	_, structured = await mcp.call_tool("enter_away_mode", {})
	assert structured == {"result": "ok"}
	assert registry.is_away_mode_active() is True


@pytest.mark.asyncio
async def test_mcp_exit_away_mode_tool_flips_registry(cfg, tmp_path):
	logger = JsonlLogger(cfg.log_path)
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_away_mode(True)
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	_, structured = await mcp.call_tool("exit_away_mode", {})
	assert structured == {"result": "ok"}
	assert registry.is_away_mode_active() is False
