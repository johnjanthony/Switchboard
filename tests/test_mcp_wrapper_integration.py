"""Integration-layer tests for the FastMCP wrapper in server/main.py.

Guards the decorator contract between _build_fastmcp wrappers and handlers.*
These tests call through the wrapper layer (via mcp.call_tool) rather than
calling handlers.* directly, so they catch argument-order bugs and missing
field declarations that bypass-at-handler tests cannot see.
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
_SESSION_ID = "test-session-id-abc"


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def mcp_instance(cfg):
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	return _build_fastmcp(handlers), registry, backend


@pytest.mark.asyncio
async def test_mcp_tool_list_includes_all_eleven_tools(cfg):
	"""All 11 expected tools must be registered in the FastMCP instance."""
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	tools = await mcp.list_tools()
	tool_names = {t.name for t in tools}
	expected = {
		"ask_human",
		"notify_human",
		"send_document_human",
		"message_and_await_agent",
		"open_conversation",
		"enter_conversation",
		"join_conversation",
		"combine_conversations",
		"lookup_conversation_ids",
		"leave_conversation",
		"set_away_mode",
	}
	assert tool_names == expected, (
		f"Tool set mismatch.\nExpected: {sorted(expected)}\nGot: {sorted(tool_names)}"
	)


def _extract_text(call_tool_result) -> str:
	"""Extract the text string from a mcp.call_tool() result.
	FastMCP returns (list_of_content, meta_dict); the first element is a list
	of TextContent objects, each with a .text attribute."""
	# Handle (content_list, meta) tuple form
	if isinstance(call_tool_result, tuple) and call_tool_result:
		call_tool_result = call_tool_result[0]
	if isinstance(call_tool_result, list) and call_tool_result:
		item = call_tool_result[0]
		if hasattr(item, "text"):
			return item.text
	return str(call_tool_result)


@pytest.mark.asyncio
async def test_wrapper_missing_cli_session_id_returns_error(mcp_instance):
	"""When cli_session_id is None (hook didn't fire), every tool that goes through
	@require_cli_session_id must return an ERROR string — not raise an exception."""
	mcp, registry, backend = mcp_instance

	result = await mcp.call_tool("notify_human", {
		"message": "hello",
		"sender": "test-agent",
		# No cli_session_id — simulates hook not firing
	})
	text = _extract_text(result)
	assert text.startswith("ERROR:"), f"Expected ERROR string, got: {text!r}"


@pytest.mark.asyncio
async def test_wrapper_ask_human_declares_cli_session_id(cfg):
	"""ask_human wrapper must accept cli_session_id as a keyword arg without raising."""
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	tools = await mcp.list_tools()
	ask_tool = next(t for t in tools if t.name == "ask_human")
	param_names = {p for p in ask_tool.inputSchema.get("properties", {})}
	assert "cli_session_id" in param_names, "ask_human must declare cli_session_id"
	assert "cwd" in param_names, "ask_human must declare cwd"
	assert "sender" in param_names, "ask_human must declare sender"


@pytest.mark.asyncio
async def test_wrapper_set_away_mode_declares_cli_session_id(cfg):
	"""set_away_mode wrapper must declare cli_session_id and cwd."""
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	tools = await mcp.list_tools()
	tool = next(t for t in tools if t.name == "set_away_mode")
	param_names = set(tool.inputSchema.get("properties", {}))
	assert "cli_session_id" in param_names
	assert "cwd" in param_names
	assert "value" in param_names


@pytest.mark.asyncio
async def test_wrapper_open_and_enter_conversation_registered(cfg):
	"""open_conversation and enter_conversation must be present in tool list."""
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	tools = await mcp.list_tools()
	tool_names = {t.name for t in tools}
	assert "open_conversation" in tool_names
	assert "enter_conversation" in tool_names


@pytest.mark.asyncio
async def test_wrapper_notify_human_with_valid_session_routes_to_handler(cfg, tmp_path):
	"""notify_human with a valid cli_session_id reaches the handler and writes a message."""
	from server.conversation_ops import _create_active_conversation_for
	logger = JsonlLogger(cfg.log_path)
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	# Pre-bind a session to a conversation so the handler doesn't hit _create_active_conversation_for
	# (that would require a real backend write).
	session_id = "sess-wrapper-test"
	from server.registry import Conversation
	conv = Conversation(id="conv-wrapper-test", title="test")
	registry.conversations["conv-wrapper-test"] = conv
	registry._session_to_conversation_id[session_id] = "conv-wrapper-test"

	result = await mcp.call_tool("notify_human", {
		"message": "status update",
		"sender": "agent-x",
		"cli_session_id": session_id,
		"cwd": _CWD,
	})
	text = _extract_text(result)
	assert text == "ERROR: John is at his desk (notification delivered to phone anyway).", f"Expected at-desk sentinel, got: {text!r}"
	assert len(backend.channel_messages) == 1, "notification must still be written even when at desk"
	assert backend.channel_messages[0]["content"] == "status update"
