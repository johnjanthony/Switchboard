"""Whole-branch review fixes for approvals: the turn-end backstop ahead of the
status write, owns() across a restart, a terminal answer that beats the hook,
and excluded tools kept out of the settle traffic."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.approvals import ApprovalRegistry
from server.gateway.dispatch import dispatch_responses
from server.main import _build_agent_status_route, _build_approvals_route
from server.messenger import IncomingResponse
from server.registry import Registry
from server.service_stop import ServiceStop
from server.session_registry import SessionRegistry
from tests.conftest import _make_loop_supervisor
from tests.test_approvals import _CardBackend, _logger, _resolve
from tests.test_approvals_backstops import _AnsweringBackend, _until
from tests.test_approvals_routes import _hook_input, _request

_INPUT = {"command": "ls", "description": "list"}


class _Clock:
	def __init__(self):
		self.t = 100.0

	def __call__(self):
		return self.t


def _world(away=False, clock=None):
	registry = Registry()
	registry.update_global_away_cache(away)
	sessions = SessionRegistry()
	backend = _CardBackend()
	kwargs = {"mono": clock} if clock is not None else {}
	approvals = ApprovalRegistry(registry, sessions, backend, _logger(), resolve_conversation=_resolve, **kwargs)
	return sessions, backend, approvals


async def _settle(approvals, tool_input=_INPUT, tool="Bash"):
	return await approvals.settle_from_terminal(
		"s1", agent_id=None, tool=tool, tool_input=tool_input, decision="accept", source="user_temporary",
	)


async def _open(approvals, tool_input=_INPUT):
	return await approvals.open(
		cli_session_id="s1", agent_id=None, tool="Bash", tool_input=tool_input, cwd="C:/Work/X",
	)


def _logged(approvals):
	return [str(c.args[0]) for c in approvals._logger.info.await_args_list if c.args]


# -- 1. the turn-end backstop runs before the status write ----------------

@pytest.mark.asyncio
async def test_a_stop_closes_main_loop_approvals_while_the_status_write_is_stalled():
	sessions, _, approvals = _world()
	main = await _open(approvals)
	never = asyncio.Event()

	async def stalled(*_args):
		await never.wait()

	handlers = MagicMock()
	handlers.handle_agent_status = AsyncMock(side_effect=stalled)
	route = _build_agent_status_route(handlers, sessions, approvals=approvals)
	task = asyncio.create_task(
		route(_request({"session_id": "s1", "state": "clear", "event": "Stop", "cwd": "C:/Work/X"}, asyncio.Event()))
	)
	try:
		assert await _until(main.future.done)
		assert main.future.result().reason == "turn_end"
		assert not task.done()
	finally:
		task.cancel()
		await asyncio.gather(task, return_exceptions=True)


# -- 2. owns() recognises its own cards across a restart -------------------

def test_owns_recognises_a_card_id_after_a_restart():
	_, _, approvals = _world()
	assert approvals.owns("conv-1", "apr-0123456789ab") is True
	assert approvals.owns("conv-1", "a1b2c3d4") is False


@pytest.mark.asyncio
async def test_answer_to_an_unknown_card_id_is_a_quiet_replay():
	_, backend, approvals = _world()
	await approvals.answer("conv-1", "apr-0123456789ab", "Allow", "answers/conv-1/apr-0123456789ab")
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert any(line.startswith("approval_replay_ignored") for line in _logged(approvals))
	assert backend.deleted_slots == ["answers/conv-1/apr-0123456789ab"]
	assert backend.messages == []


@pytest.mark.asyncio
async def test_dispatch_after_a_restart_does_not_send_a_withdrawn_notice():
	registry = Registry()
	backend = _AnsweringBackend()
	logger = _logger()
	approvals = ApprovalRegistry(registry, SessionRegistry(), backend, logger, resolve_conversation=_resolve)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup, approvals=approvals))
	try:
		slot = "answers/conv-1/apr-0123456789ab"
		backend.answers.put_nowait(IncomingResponse(correlation="conv-1", text="Allow", slot=slot, request_id="apr-0123456789ab"))
		assert await _until(lambda: backend.deleted_slots == [slot])
		backend.send_stale_reply_notice.assert_not_awaited()
	finally:
		task.cancel()
		await asyncio.gather(task, return_exceptions=True)


# -- 3. a terminal answer that beats the hook ------------------------------

@pytest.mark.asyncio
async def test_a_terminal_answer_before_the_hook_opens_the_approval_already_released():
	sessions, backend, approvals = _world(away=True)
	assert await _settle(approvals) is False
	approval = await _open(approvals)
	assert approval.closed is True
	assert approval.future.result().kind == "released"
	assert approval.future.result().reason == "terminal_before_hook:user_temporary:accept"
	assert approvals.open_approvals() == []
	rec = sessions.get("s1")
	assert rec is None or rec.awaiting_approval is False
	assert backend.cards() == []
	logged = _logged(approvals)
	assert any(l.startswith("approval_opened") for l in logged)
	assert any(
		l.startswith("approval_closed") and "reason=terminal_before_hook:user_temporary:accept" in l for l in logged
	)


@pytest.mark.asyncio
async def test_the_route_answers_204_at_once_for_a_dialog_already_answered_at_the_terminal():
	_, _, approvals = _world()
	await _settle(approvals, tool_input=_hook_input()["tool_input"])
	route = _build_approvals_route(approvals, ServiceStop())
	response = await asyncio.wait_for(route(_request(_hook_input(), asyncio.Event())), 2)
	assert response.status_code == 204
	assert approvals.open_approvals() == []


@pytest.mark.asyncio
async def test_an_early_report_older_than_ten_seconds_is_ignored():
	clock = _Clock()
	_, _, approvals = _world(clock=clock)
	await _settle(approvals)
	clock.t += 10.5
	approval = await _open(approvals)
	assert approval.closed is False
	assert approvals.open_approvals() == [approval]


@pytest.mark.asyncio
async def test_an_early_report_is_consumed_once():
	_, _, approvals = _world()
	await _settle(approvals)
	first = await _open(approvals)
	second = await _open(approvals)
	assert first.closed is True
	assert second.closed is False
	assert approvals.open_approvals() == [second]


@pytest.mark.asyncio
async def test_an_early_report_for_another_input_is_not_matched():
	_, _, approvals = _world()
	await _settle(approvals, tool_input={"command": "rm -rf x"})
	approval = await _open(approvals)
	assert approval.closed is False


# -- 4. excluded tools are not settle traffic ------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode"])
async def test_settling_an_excluded_tool_is_neither_logged_nor_remembered(tool):
	_, _, approvals = _world()
	assert await _settle(approvals, tool=tool) is False
	assert not any(line.startswith("approval_settle_unmatched") for line in _logged(approvals))
	assert len(approvals._early) == 0
