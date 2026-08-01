"""ask_human(background=True): posts the question and returns a pending
envelope immediately instead of blocking for John's reply. The answer is
delivered by machinery built and tested elsewhere (the dispatch resolve path
and Registry.add_background's append semantics); these tests cover the
handler branch that creates/appends the background slot and the Firebase
record writes it triggers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.rate_limiter import RateLimiter
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


def _build(tmp_path: Path, *, away: bool, rate_per_minute: int = 10, backend_cls=RecordingBackend):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)
	registry = Registry()
	registry.global_away_mode = away
	conv = make_active_conversation(conversation_id="conv-bg", member_session_id="s-bg", sender="Claude")
	registry.conversations["conv-bg"] = conv
	registry.bind_session("s-bg", "conv-bg")
	backend = backend_cls()
	handlers = build_tool_handlers(
		cfg, registry, backend, JsonlLogger(cfg.log_path), limiter=RateLimiter(rate_per_minute),
	)
	return registry, backend, handlers


class _FailOnDemandAppendBackend(RecordingBackend):
	"""update_pending_question_text raises when armed. Used to exercise the
	append-failure rollback: a failed Firebase write on the append path must
	not leave the in-memory question text grown while Firebase still holds
	the pre-append text (a retry would otherwise double-append)."""

	def __init__(self) -> None:
		super().__init__()
		self.fail_next_update = False

	async def update_pending_question_text(self, conversation_id, request_id, question_text):
		if self.fail_next_update:
			self.fail_next_update = False
			raise RuntimeError("update boom")
		return await super().update_pending_question_text(conversation_id, request_id, question_text)


@pytest.mark.asyncio
async def test_first_background_ask_returns_pending_immediately(tmp_path: Path):
	registry, backend, handlers = _build(tmp_path, away=True)

	result = await handlers.ask_human(
		"q1?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)

	payload = json.loads(result)
	assert payload["status"] == "pending"
	request_id = payload["request_id"]

	record = registry.find_by_request_id("conv-bg", request_id)
	assert record is not None and record.background is True

	questions = [m for m in backend.channel_messages if m["message_type"] == "question"]
	assert len(questions) == 1
	assert questions[0]["request_id"] == request_id

	assert len(backend.pending_question_records) == 1
	written = backend.pending_question_records[0]
	assert written["request_id"] == request_id
	assert written["background"] is True


@pytest.mark.asyncio
async def test_second_background_ask_appends_to_the_same_slot(tmp_path: Path):
	registry, backend, handlers = _build(tmp_path, away=True)

	result1 = await handlers.ask_human(
		"q1?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	request_id = json.loads(result1)["request_id"]

	result2 = await handlers.ask_human(
		"q2?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	payload2 = json.loads(result2)
	assert payload2["status"] == "pending"
	assert payload2["request_id"] == request_id, "the second ask must reuse the first's request_id"

	record = registry.find_by_request_id("conv-bg", request_id)
	assert record.question == "q1?\n\nAlso: q2?"

	questions = [m for m in backend.channel_messages if m["message_type"] == "question"]
	assert len(questions) == 2
	assert all(q["request_id"] == request_id for q in questions)

	# No second registration - only the append-text update.
	assert len(backend.pending_question_records) == 1
	assert backend.pending_question_text_updates == [("conv-bg", request_id, "q1?\n\nAlso: q2?")]


@pytest.mark.asyncio
async def test_background_ask_at_desk_returns_sentinel_and_creates_no_record(tmp_path: Path):
	registry, backend, handlers = _build(tmp_path, away=False)

	result = await handlers.ask_human(
		"q1?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)

	assert result == "ERROR: John is at his desk. State your question in the terminal and continue working."

	notifications = [m for m in backend.channel_messages if m["message_type"] == "notify"]
	assert len(notifications) == 1
	assert notifications[0]["content"] == "q1?"

	assert registry.pending_for_conversation("conv-bg") == []
	assert backend.pending_question_records == []


@pytest.mark.asyncio
async def test_blocking_ask_from_same_session_leaves_background_slot_untouched(tmp_path: Path):
	registry, backend, handlers = _build(tmp_path, away=True)

	bg_result = await handlers.ask_human(
		"bg question?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	bg_request_id = json.loads(bg_result)["request_id"]

	blocking_task = asyncio.create_task(handlers.ask_human(
		"blocking question?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X",
	))
	for _ in range(5):
		await asyncio.sleep(0)

	assert registry.pending_count == 2
	bg_record = registry.find_by_request_id("conv-bg", bg_request_id)
	assert bg_record is not None and bg_record.background is True
	assert bg_record.question == "bg question?"

	blocking_record = registry.live_blocking_pending("conv-bg", "s-bg")
	assert blocking_record is not None
	assert registry.resolve("conv-bg", blocking_record.request_id, "yes") == blocking_record.request_id
	assert await asyncio.wait_for(blocking_task, 5) == "yes"

	# Still there and untouched after the blocking ask resolved.
	assert registry.find_by_request_id("conv-bg", bg_request_id) is not None


@pytest.mark.asyncio
async def test_background_ask_still_rate_limited(tmp_path: Path):
	registry, backend, handlers = _build(tmp_path, away=True, rate_per_minute=1)

	result1 = await handlers.ask_human(
		"q1?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	assert json.loads(result1)["status"] == "pending"

	result2 = await handlers.ask_human(
		"q2?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	assert result2.startswith("ERROR: rate limit exceeded")

	assert registry.pending_count == 1
	assert len(backend.pending_question_records) == 1


@pytest.mark.asyncio
async def test_failed_append_write_rolls_back_the_in_memory_growth(tmp_path: Path):
	"""A Firebase failure on the append path must not leave the in-memory
	question text grown while the persisted record still holds the
	pre-append text - otherwise a retry compounds on top of an append that
	was never actually persisted, producing a doubled question."""
	registry, backend, handlers = _build(tmp_path, away=True, backend_cls=_FailOnDemandAppendBackend)

	result1 = await handlers.ask_human(
		"q1?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	request_id = json.loads(result1)["request_id"]

	backend.fail_next_update = True
	result2 = await handlers.ask_human(
		"q2?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	assert result2.startswith("ERROR:")

	record = registry.find_by_request_id("conv-bg", request_id)
	assert record is not None
	assert record.question == "q1?", "the failed append must be rolled back to the pre-append text"

	# Retry the same append; this time the write succeeds.
	result3 = await handlers.ask_human(
		"q2?", "Claude", cli_session_id="s-bg", cwd="C:/Work/X", background=True,
	)
	payload3 = json.loads(result3)
	assert payload3["status"] == "pending"
	assert payload3["request_id"] == request_id

	final_record = registry.find_by_request_id("conv-bg", request_id)
	assert final_record.question == "q1?\n\nAlso: q2?"
	assert final_record.question.count("\n\nAlso: ") == 1, "must not double-append after the rollback"
