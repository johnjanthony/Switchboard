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

_CWD = "c:/work/sw"


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.mark.asyncio
async def test_mcp_tool_list_matches_current_surface(cfg):
	"""Guards the registered tool surface: the exact set of tools exposed via MCP
	must match the conversations-redesign surface.

	v2 surface (10 tools):
	- enter_away_mode / exit_away_mode: retired
	- open_conversation, enter_conversation: new additions (Fix 1)
	- set_away_mode, combine_conversations, leave_conversation: present
	"""
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
		"open_conversation",
		"enter_conversation",
		"lookup_conversation_ids",
		"leave_conversation",
		"set_away_mode",
		"combine_conversations",
	}
