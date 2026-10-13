"""The approval backstops (session end, main-loop turn end), the phone answer's
path through dispatch_responses, and /stats needs_you's approval reason."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.approvals import ApprovalRegistry
from server.gateway.dispatch import dispatch_responses
from server.main import _build_agent_status_route, _build_session_end_route, _build_stats_route
from server.messenger import IncomingResponse
from server.registry import Registry
from server.session_registry import SessionRegistry
from tests.conftest import _make_loop_supervisor
from tests.test_approvals import _CardBackend, _logger, _resolve
from tests.test_approvals_routes import _request
from tests.test_stats_endpoint import _FakeBackend, _FakeLoopSup, _make_request, _registry


def _world(away=False):
	registry = Registry()
	registry.update_global_away_cache(away)
	sessions = SessionRegistry()
	backend = _CardBackend()
	approvals = ApprovalRegistry(registry, sessions, backend, _logger(), resolve_conversation=_resolve)
	return registry, sessions, backend, approvals


async def _open(approvals, agent_id=None):
	return await approvals.open(
		cli_session_id="s1", agent_id=agent_id, tool="Bash", tool_input={"command": "ls"}, cwd="C:/Work/X",
	)


@pytest.mark.asyncio
async def test_a_session_end_closes_the_sessions_approvals():
	registry, sessions, _, approvals = _world()
	approval = await _open(approvals)
	route = _build_session_end_route(registry, sessions, None, None, approvals=approvals)
	response = await route(_request({"session_id": "s1", "reason": "prompt_input_exit"}, asyncio.Event()))
	assert response.status_code == 200
	assert approval.future.result().reason == "session_end"


@pytest.mark.asyncio
async def test_a_stop_status_closes_main_loop_approvals_only():
	_, sessions, _, approvals = _world()
	main = await _open(approvals)
	sub = await _open(approvals, agent_id="agent-1")
	handlers = MagicMock()
	handlers.handle_agent_status = AsyncMock()
	route = _build_agent_status_route(handlers, sessions, approvals=approvals)
	await route(_request(
		{"session_id": "s1", "state": "thinking", "event": "PostToolUse", "cwd": "C:/Work/X"}, asyncio.Event(),
	))
	assert not main.future.done()
	await route(_request({"session_id": "s1", "state": "clear", "event": "Stop", "cwd": "C:/Work/X"}, asyncio.Event()))
	assert main.future.result().reason == "turn_end"
	assert not sub.future.done()


class _AnsweringBackend(_CardBackend):
	def __init__(self):
		super().__init__()
		self.answers = asyncio.Queue()
		self.send_stale_reply_notice = AsyncMock()

	async def poll_responses(self):
		while True:
			yield await self.answers.get()


async def _until(cond, cap=400, dt=0.005):
	for _ in range(cap):
		if cond():
			return True
		await asyncio.sleep(dt)
	return cond()


@pytest.mark.asyncio
async def test_a_phone_answer_to_a_card_reaches_its_approval_and_a_replay_is_quiet():
	registry = Registry()
	registry.update_global_away_cache(True)
	backend = _AnsweringBackend()
	logger = _logger()
	approvals = ApprovalRegistry(registry, SessionRegistry(), backend, logger, resolve_conversation=_resolve)
	approval = await _open(approvals)
	card_id = approval.card_id
	response = IncomingResponse(correlation="conv-1", text="Allow", slot=f"answers/conv-1/{card_id}", request_id=card_id)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup, approvals=approvals))
	try:
		backend.answers.put_nowait(response)
		assert await _until(approval.future.done)
		assert approval.future.result().decision == {"behavior": "allow"}
		backend.answers.put_nowait(response)
		assert await _until(lambda: len(backend.deleted_slots) == 2)
		backend.send_stale_reply_notice.assert_not_awaited()
	finally:
		task.cancel()
		await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_an_answer_no_approval_owns_still_takes_the_ask_path():
	registry = Registry()
	backend = _AnsweringBackend()
	logger = _logger()
	approvals = ApprovalRegistry(registry, SessionRegistry(), backend, logger, resolve_conversation=_resolve)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup, approvals=approvals))
	try:
		backend.answers.put_nowait(IncomingResponse(correlation="conv-1", text="hi", slot="answers/conv-1/r1", request_id="r1"))
		assert await _until(lambda: backend.send_stale_reply_notice.await_count == 1)
	finally:
		task.cancel()
		await asyncio.gather(task, return_exceptions=True)


def _ago(seconds):
	return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.mark.asyncio
async def test_needs_you_reports_an_open_dialog_as_approval():
	sessions = SessionRegistry()
	sessions.set_awaiting_approval("s-dialog", _ago(120))
	route = _build_stats_route(_registry(active=1), _FakeBackend([]), {"d": _FakeLoopSup(0)}, session_registry=sessions)
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s-dialog"]["reason"] == "approval"
	assert body["needs_you"]["s-dialog"]["age_seconds"] >= 120.0


@pytest.mark.asyncio
async def test_an_ask_wins_over_an_approval_for_one_session():
	registry = _registry(active=1)
	registry.add_parked(conversation_id="a0", cli_session_id="s1", sender="Claude", request_id="r1",
		started_at=datetime.now(timezone.utc) - timedelta(seconds=30))
	sessions = SessionRegistry()
	sessions.set_awaiting_approval("s1", _ago(300))
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)}, session_registry=sessions)
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s1"]["reason"] == "ask"


@pytest.mark.asyncio
async def test_a_terminal_session_with_a_stale_mark_is_left_out():
	sessions = SessionRegistry()
	sessions.set_awaiting_approval("s1", _ago(60))
	sessions.get("s1").state = "lost"
	route = _build_stats_route(_registry(active=1), _FakeBackend([]), {"d": _FakeLoopSup(0)}, session_registry=sessions)
	body = json.loads((await route(_make_request())).body)
	assert "s1" not in body["needs_you"]
