"""Chunk 7 (T-001 parked pendings): persistence fields, parked resolve via the
dispatch loop, bulk-respond drain, and the TTL sweep."""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import server.firebase as fb_module
from server.firebase import FirebaseBackend
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse
from server.registry import Conversation, ConversationMember, Registry
from server.session_registry import SessionRegistry

_CONV = "conv-1"


@pytest.mark.asyncio
async def test_add_pending_question_record_persists_session_id_and_timestamp(monkeypatch):
	backend = FirebaseBackend.__new__(FirebaseBackend)
	captured = {}

	def reference(path):
		ref = MagicMock()
		def _set(payload):
			captured["path"] = path
			captured["payload"] = payload
		ref.set = _set
		return ref

	mock_db = MagicMock()
	mock_db.reference = reference
	monkeypatch.setattr(fb_module, "db", mock_db)

	await backend.add_pending_question_record(
		"conv-1", "req-1", sender="Claude", msg_id="m-1", question_text="Deploy?",
		suggestions=None, cli_session_id="sess-A", asked_at="2026-07-07T12:00:00+00:00",
	)
	assert captured["path"] == "conversations/conv-1/pending_questions/req-1"
	assert captured["payload"]["cliSessionId"] == "sess-A"
	assert captured["payload"]["askedAt"] == "2026-07-07T12:00:00+00:00"
	assert captured["payload"]["questionText"] == "Deploy?"
	assert captured["payload"]["cancelled"] is False


class _AnswerBackend:
	"""dispatch_responses surface: poll_responses yields the scripted answers,
	then parks forever (the loop is cancelled by the test)."""

	def __init__(self, responses):
		self._responses = responses
		self.deleted_slots = []
		self.stale_notices = []
		self.history = []
		self.pending_removed = []

	async def poll_responses(self):
		for r in self._responses:
			yield r
		await asyncio.Event().wait()

	async def delete_response_slot(self, slot):
		self.deleted_slots.append(slot)

	async def send_stale_reply_notice(self, conversation_id, sender):
		self.stale_notices.append((conversation_id, sender))

	async def write_conversation_message(self, conversation_id, sender, message_type, text, **kwargs):
		self.history.append((conversation_id, sender, message_type, text, kwargs.get("attached_to_msg_id")))
		return None, "m-reply"

	async def remove_pending_question_record(self, conversation_id, request_id):
		self.pending_removed.append((conversation_id, request_id))

	async def send_resolution_confirmation(self, request_id, conversation_id, correlation, response_text=None):
		pass


async def _pump(ticks=25):
	"""Real-time pump: asyncio.to_thread logger writes in the dispatch loop and
	spawned history-write tasks need real time to complete between scripted
	answers (bare sleep(0) yields give the thread pool none). Mirrors the
	_until/_settle real-sleep convention in test_dispatch_replayed_answer_false_notice.py."""
	for _ in range(ticks):
		await asyncio.sleep(0.02)


async def _cancel(task):
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_parked_record_resolves_with_history_notice_and_cleanup(tmp_path):
	from server.gateway.dispatch import dispatch_responses
	from tests.conftest import _make_loop_supervisor

	registry = Registry()
	session_registry = SessionRegistry()
	session_registry.record_session_start("sess-A", cwd="C:/Work/X")
	registry.add_parked(_CONV, "sess-A", "Claude", "req-1", msg_id="m-q", question="Deploy the fix?")
	registry.find_by_request_id(_CONV, "req-1").notices.append("JOIN NOTICE: John convened you")

	backend = _AnswerBackend([
		IncomingResponse(correlation=_CONV, text="yes, ship it", slot=f"{_CONV}/answers/req-1",
			request_id="req-1", sender="John"),
	])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup, session_registry=session_registry)
	)
	await _pump()

	assert registry.pending_count == 0
	assert registry.was_recently_resolved(_CONV, "req-1")
	assert (_CONV, "John", "human", "yes, ship it", "m-q") in backend.history
	assert (_CONV, "req-1") in backend.pending_removed
	assert f"{_CONV}/answers/req-1" in backend.deleted_slots
	notices = session_registry.pop_notices("sess-A")
	assert notices == [
		"JOIN NOTICE: John convened you",
		"John answered your earlier question 'Deploy the fix?': yes, ship it",
	]
	assert backend.stale_notices == []
	await _cancel(task)


@pytest.mark.asyncio
async def test_replay_after_parked_resolve_is_quiet(tmp_path):
	from server.gateway.dispatch import dispatch_responses
	from tests.conftest import _make_loop_supervisor

	registry = Registry()
	session_registry = SessionRegistry()
	session_registry.record_session_start("sess-A", cwd="C:/Work/X")
	registry.add_parked(_CONV, "sess-A", "Claude", "req-1", msg_id="m-q", question="Deploy?")

	answer = IncomingResponse(correlation=_CONV, text="yes", slot=f"{_CONV}/answers/req-1",
		request_id="req-1", sender="John")
	backend = _AnswerBackend([answer, answer])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup, session_registry=session_registry)
	)
	await _pump()

	assert backend.stale_notices == []
	assert len([h for h in backend.history if h[2] == "human"]) == 1
	assert len(backend.deleted_slots) == 2
	await _cancel(task)


@pytest.mark.asyncio
async def test_bulk_respond_send_default_resolves_parked_records(tmp_path):
	from server.gateway.bulk_respond import _apply_bulk_respond_decision

	registry = Registry()
	session_registry = SessionRegistry()
	session_registry.record_session_start("sess-A", cwd="C:/Work/X")
	registry.add_parked(_CONV, "sess-A", "Claude", "req-1", msg_id="m-q", question="Deploy?")

	backend = _AnswerBackend([])
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	commit = await _apply_bulk_respond_decision(
		registry, backend, logger, decision="send_default", default_text="handled at desk",
		session_registry=session_registry,
	)
	assert commit is True
	assert registry.pending_count == 0
	assert (_CONV, "req-1") in backend.pending_removed
	assert (_CONV, "John", "human", "handled at desk", "m-q") in backend.history
	assert session_registry.pop_notices("sess-A") == [
		"John answered your earlier question 'Deploy?': handled at desk",
	]


@pytest.mark.asyncio
async def test_parked_ttl_sweep_cancels_only_expired_parked(tmp_path):
	from server.gateway.dispatch import _parked_sweep_once

	registry = Registry()
	registry.add_parked("conv-1", "sess-A", "Claude", "req-old", question="Old?")
	registry.find_by_request_id("conv-1", "req-old").started_at = (
		datetime.now(timezone.utc) - timedelta(hours=73)
	)
	registry.add_parked("conv-2", "sess-B", "Claude", "req-fresh", question="Fresh?")
	live_fut = registry.add("conv-3", "sess-C", "Claude", "req-live")

	backend = MagicMock()
	backend.mark_question_cancelled = AsyncMock()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	count = await _parked_sweep_once(registry, backend, logger, max_age_hours=72)
	assert count == 1
	backend.mark_question_cancelled.assert_awaited_once_with("conv-1", "req-old")
	assert registry.find_by_request_id("conv-1", "req-old") is None
	assert registry.find_by_request_id("conv-2", "req-fresh") is not None
	assert not live_fut.cancelled()
	assert registry.pending_count == 2


@pytest.mark.asyncio
async def test_session_sweep_loop_runs_parked_sweep_when_wired(tmp_path):
	from server.gateway.dispatch import dispatch_session_sweep
	from server.session_registry import SessionRegistry
	from server.widget_snapshot import WidgetSnapshotStore
	from tests.conftest import _make_loop_supervisor

	registry = Registry()
	registry.add_parked("conv-1", "sess-A", "Claude", "req-old", question="Old?")
	registry.find_by_request_id("conv-1", "req-old").started_at = (
		datetime.now(timezone.utc) - timedelta(hours=73)
	)
	backend = MagicMock()
	backend.mark_question_cancelled = AsyncMock()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_loop_supervisor(None, logger, "dispatch_session_sweep")

	task = asyncio.create_task(
		dispatch_session_sweep(
			SessionRegistry(), WidgetSnapshotStore(), logger, supervisor,
			lost_after_seconds=900, retention_hours=72, interval=10.0,
			registry=registry, backend=backend,
		)
	)
	await _pump()
	assert registry.parked_count == 0
	backend.mark_question_cancelled.assert_awaited_once_with("conv-1", "req-old")
	await _cancel(task)
