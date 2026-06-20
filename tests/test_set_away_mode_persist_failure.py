"""F-67: when the Firebase persist of the away-mode flag raises, set_away_mode
must return an ERROR (not "ok") so the caller learns the phone did not see the
flip. The in-memory flag still reflects the requested value."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


class FailingPersistBackend(RecordingBackend):
	async def set_global_away_mode(self, value: bool) -> None:
		raise RuntimeError("firebase down")


@pytest.mark.asyncio
async def test_persist_failure_returns_error_but_flag_set(tmp_path: Path):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))
	registry = Registry()
	registry.global_away_mode = False
	conv = make_active_conversation(conversation_id="conv-p1", member_session_id="s-p1", sender="Claude")
	registry.conversations["conv-p1"] = conv
	registry.bind_session("s-p1", "conv-p1")
	handlers = build_tool_handlers(cfg, registry, FailingPersistBackend(), JsonlLogger(cfg.log_path))

	result = await handlers.set_away_mode(True, cli_session_id="s-p1", cwd="C:/Work/X")

	assert result.startswith("ERROR"), f"persist failure must surface as ERROR, got: {result!r}"
	assert "persist failed" in result.lower()
	assert registry.global_away_mode is True, "the in-memory flag still reflects the requested value"
