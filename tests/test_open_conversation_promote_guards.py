"""F-72: open_conversation's promote path must not promote an Ended
conversation, and must error if the bound caller is not a member."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


def _handlers(tmp_path: Path, registry: Registry):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))
	return build_tool_handlers(cfg, registry, RecordingBackend(), JsonlLogger(cfg.log_path))


@pytest.mark.asyncio
async def test_promote_ended_conversation_is_rejected(tmp_path):
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-e1", member_session_id="s-e1", sender="Claude")
	conv.state = "ended"
	registry.conversations["conv-e1"] = conv
	registry.bind_session("s-e1", "conv-e1")
	handlers = _handlers(tmp_path, registry)

	result = await handlers.open_conversation("Claude", cli_session_id="s-e1", cwd="C:/Work/X")

	assert result.startswith("ERROR")
	assert registry.open_conversation_id != "conv-e1", "must not promote an Ended conversation"
