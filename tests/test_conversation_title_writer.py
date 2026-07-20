"""P5-3 (M3): a title change after creation must reach Firebase via a
meta/title writer. F-80: write_conversation_meta must use update(), not
set(), so it cannot clobber sibling meta fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


class TitleTrackingBackend(RecordingBackend):
	def __init__(self) -> None:
		super().__init__()
		self.titles_written: list[tuple[str, str]] = []

	async def write_conversation_title(self, conversation_id: str, title: str) -> None:
		self.titles_written.append((conversation_id, title))


@pytest.mark.asyncio
async def test_message_and_await_agent_title_change_reaches_backend(tmp_path: Path):
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=0.05, log_path=str(tmp_path / "server.log"))
	registry = Registry()
	# Two alive members so the speak does not take the sole-alive empty path.
	conv = make_active_conversation(conversation_id="conv-t1", member_session_id="s-1", sender="Claude")
	from server.registry import ConversationMember
	conv.members_active["s-2"] = ConversationMember(
		cli_session_id="s-2", sender="Peer", cwd="C:/Work/X", surface="windows", joined_at=0.0,
	)
	registry.conversations["conv-t1"] = conv
	registry.bind_session("s-1", "conv-t1")
	backend = TitleTrackingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	# A speak that times out (no peer wakes it) but sets a new title.
	await handlers.message_and_await_agent(
		"Claude", message="status update", title="New Scope",
		cli_session_id="s-1", cwd="C:/Work/X",
	)

	assert ("conv-t1", "New Scope") in backend.titles_written, \
		f"post-creation title change must reach the backend; got {backend.titles_written}"
