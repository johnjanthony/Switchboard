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
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.mark.asyncio
async def test_mcp_notify_human_tool_is_registered_and_invocable(cfg):
	"""Build the real FastMCP instance, list its tools, and invoke
	notify_human via FastMCP's programmatic call_tool API. Confirms the
	decorator wiring reaches the underlying handler and that the return
	value round-trips through the MCP envelope as 'ok'."""
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	# All four tools should be registered.
	tools = await mcp.list_tools()
	tool_names = {t.name for t in tools}
	assert tool_names == {"ask_human", "notify_human", "send_document_human", "message_and_await_agent"}, (
		f"expected all four tools to be registered, got {tool_names}"
	)

	# Invoke notify_human. FastMCP.call_tool returns a tuple:
	# (content_blocks, structured_result). The structured result
	# carries the handler's return value under the 'result' key.
	content, structured = await mcp.call_tool(
		"notify_human",
		{"message": "hello world", "agent_id": "IR2"},
	)

	assert structured == {"result": "ok"}
	# The text content block should also carry the "ok" payload.
	assert any(getattr(block, "text", "") == "ok" for block in content), (
		f"expected an 'ok' text block, got {content}"
	)
	# And most importantly — the call actually reached the handler,
	# which called through to the backend.
	assert backend.sent_notifications == [("IR2", "hello world")]
