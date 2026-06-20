"""F-69(g): when a resume ends its source conversation and that source was the
open conversation, the open-pointer clear must be persisted to Firebase via
set_open_conversation_id(None), not just cleared in memory; otherwise the
phone's open badge sticks on the Ended source until restart."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry, Conversation, ConversationMember
from server.spawn import SpawnHandler


def _config(tmp_path):
	return Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "server.log"))


@pytest.mark.asyncio
async def test_source_ended_resume_persists_open_pointer_clear(tmp_path, monkeypatch):
	registry = Registry()
	source = Conversation(id="conv-src", title="Src")
	dormant = ConversationMember(
		cli_session_id="s-dormant", sender="Claude", cwd="C:/Work/X", surface="windows", joined_at=0.0,
	)
	dormant.alive = False
	source.members_active["Claude"] = dormant
	registry.conversations["conv-src"] = source
	# Source is the open conversation; the resume will end it (sole member resumes out).
	registry.open_conversation_id = "conv-src"

	backend = MagicMock()
	backend.write_conversation_meta = AsyncMock()
	backend.write_conversation_message = AsyncMock(return_value="key")
	backend.write_conversation_member = AsyncMock()
	backend.remove_conversation_member = AsyncMock()
	backend.set_conversation_state = AsyncMock()
	backend.set_open_conversation_id = AsyncMock()
	backend.set_global_away_mode = AsyncMock()
	backend.send_text = AsyncMock()
	backend.mark_question_cancelled = AsyncMock()

	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handler = SpawnHandler(_config(tmp_path), backend, logger, registry)
	# Degrade the interactive-session gate open so the launcher path is reached.
	monkeypatch.setattr(handler, "_user_has_interactive_session", AsyncMock(return_value=True))
	monkeypatch.setattr(handler, "_invoke_launcher", AsyncMock())

	await handler.handle_resume({"type": "resume", "source_conversation_id": "conv-src", "issued_at": "x"})

	# Let scheduled background coroutines run.
	import asyncio
	await asyncio.sleep(0.05)

	assert registry.open_conversation_id is None, "open pointer cleared in memory"
	backend.set_open_conversation_id.assert_awaited_with(None)
	assert backend.set_open_conversation_id.await_count >= 1, \
		"the open-pointer clear must be persisted to Firebase"
