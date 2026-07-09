"""P1-1 (H05): an ask_human timeout must mark the question message cancelled
so the phone drops it from its pending list. The phone derives "pending"
purely from message flags; removing only the registry entry and the
pending_questions record leaves the question pending on the phone forever."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from tests.conftest import make_active_conversation, make_registry_with_loopback
from tests.test_gateway_notify_human import RecordingBackend


class CancelTrackingBackend(RecordingBackend):
	def __init__(self) -> None:
		super().__init__()
		self.cancelled_questions: list[tuple[str, str]] = []

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		self.cancelled_questions.append((conversation_id, request_id))


@pytest.mark.asyncio
async def test_timeout_marks_question_cancelled(tmp_path: Path):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0.05,
		log_path=str(tmp_path / "server.log"),
	)
	registry = make_registry_with_loopback()  # away mode ON: ask_human blocks
	conv = make_active_conversation(conversation_id="conv-t1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-t1"] = conv
	registry.bind_session("s-1", "conv-t1")
	backend = CancelTrackingBackend()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human(
		"still there?", "Claude",
		cli_session_id="s-1", cwd="C:/Work/X",
	)

	data = json.loads(result)
	assert data["status"] == "timeout", f"expected the timeout envelope, got: {result!r}"
	# The question's Firebase message is marked cancelled (which also clears
	# its pending_questions record; firebase.py mark_question_cancelled)
	assert len(backend.cancelled_questions) == 1, \
		f"timeout must mark the question cancelled; got: {backend.cancelled_questions}"
	# No pending entry survives
	assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_timeout_pops_registry_before_firebase_write(tmp_path: Path):
	# REV-108: the pop must precede the first await in the timeout arm, so a
	# just-landed answer can never resolve "successfully" against a future
	# that already timed out. Instrument the backend: at the moment
	# mark_question_cancelled runs, the registry must already be empty.
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0.05,
		log_path=str(tmp_path / "server.log"),
	)
	registry = make_registry_with_loopback()  # away mode ON: ask_human blocks
	conv = make_active_conversation(conversation_id="conv-timeout-race", member_session_id="s-timeout-race", sender="Claude")
	registry.conversations["conv-timeout-race"] = conv
	registry.bind_session("s-timeout-race", "conv-timeout-race")
	backend = CancelTrackingBackend()
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	observed = {}
	original = backend.mark_question_cancelled

	async def spy(conv_id, req_id):
		observed["pending_at_mark"] = registry.pending_count
		await original(conv_id, req_id)

	backend.mark_question_cancelled = spy

	result = await handlers.ask_human(
		"Q", "Claude",
		cli_session_id="s-timeout-race", cwd="c:/work/sw",
	)

	assert json.loads(result) == {"status": "timeout"}
	assert observed["pending_at_mark"] == 0
	assert registry.pending_count == 0
