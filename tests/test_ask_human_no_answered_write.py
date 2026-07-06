"""F-66/F-73 (decided delete): a successful ask_human resolution no longer
writes answered_question_msg_ids. The phone derives answered-state from
message flags, so the write had no reader."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from tests.conftest import make_registry_with_loopback, make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


class AnsweredTrackingBackend(RecordingBackend):
	def __init__(self) -> None:
		super().__init__()
		self.answered_calls: list[tuple[str, str]] = []

	# If the method still exists post-implementation, this would record calls.
	async def mark_question_answered(self, conversation_id: str, msg_id: str) -> None:
		self.answered_calls.append((conversation_id, msg_id))


@pytest.mark.asyncio
async def test_resolved_ask_human_does_not_mark_answered(tmp_path: Path):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))
	registry = make_registry_with_loopback()  # away ON: ask_human blocks
	conv = make_active_conversation(conversation_id="conv-q1", member_session_id="s-q1", sender="Claude")
	registry.conversations["conv-q1"] = conv
	registry.bind_session("s-q1", "conv-q1")
	backend = AnsweredTrackingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	async def _resolve_soon():
		await asyncio.sleep(0.02)
		pending = registry.pending_for_conversation("conv-q1")[0]
		registry.resolve("conv-q1", pending.request_id, "the answer")

	asyncio.create_task(_resolve_soon())
	result = await handlers.ask_human("ready?", "Claude", cli_session_id="s-q1", cwd="C:/Work/X")
	await asyncio.sleep(0.05)  # let post-resolve background tasks run

	assert result == "the answer"
	assert backend.answered_calls == [], \
		f"resolution must not write answered_question_msg_ids; got {backend.answered_calls}"
