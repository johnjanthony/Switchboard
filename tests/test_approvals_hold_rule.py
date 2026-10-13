"""The desk hold rule: at the desk only a main-loop request from a session not
known to be non-interactive is held; away mode holds everything, and turning
away mode off releases what the rule would not have held."""

import asyncio

import pytest

from server.approvals import ApprovalRegistry, Outcome
from server.gateway.bg_tasks import drain_bg_tasks
from server.main import _build_approvals_route, _build_session_start_route
from server.registry import Registry
from server.session_registry import SessionRegistry
from tests.test_approvals import _CardBackend, _logger, _resolve
from tests.test_approvals_routes import _hook_input, _request


def _world(away=False):
	registry = Registry()
	registry.update_global_away_cache(away)
	sessions = SessionRegistry()
	backend = _CardBackend()
	approvals = ApprovalRegistry(registry, sessions, backend, _logger(), resolve_conversation=_resolve)
	registry.add_away_observer(approvals.on_away_change)
	return registry, sessions, backend, approvals


async def _wait_open(approvals, count):
	for _ in range(200):
		if len(approvals.open_approvals()) >= count:
			return
		await asyncio.sleep(0.005)
	raise AssertionError(f"expected {count} open approvals, saw {len(approvals.open_approvals())}")


async def _release_all(approvals, tasks):
	for approval in approvals.open_approvals():
		await approvals.close(approval, Outcome("released", reason="test"))
	for task in tasks:
		await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_at_the_desk_a_subagent_request_gets_204_at_once_and_opens_nothing():
	_, sessions, backend, approvals = _world(away=False)
	route = _build_approvals_route(approvals, None)
	response = await asyncio.wait_for(
		route(_request(_hook_input(agent_id="a1"), asyncio.Event())), 1,
	)
	assert response.status_code == 204
	assert approvals.open_approvals() == []
	assert not any("approval_opened" in str(c) for c in approvals._logger.info.call_args_list)
	rec = sessions.get("s1")
	assert rec is None or rec.awaiting_approval is False


@pytest.mark.asyncio
async def test_at_the_desk_a_non_interactive_main_loop_request_gets_204_at_once():
	_, sessions, backend, approvals = _world(away=False)
	sessions.record_session_start("s1", cwd="C:/Work/X", interactive=False)
	route = _build_approvals_route(approvals, None)
	response = await asyncio.wait_for(route(_request(_hook_input(), asyncio.Event())), 1)
	assert response.status_code == 204
	assert approvals.open_approvals() == []
	assert sessions.get("s1").awaiting_approval is False


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive", [True, None])
async def test_at_the_desk_an_interactive_or_unknown_main_loop_request_is_held(interactive):
	_, sessions, backend, approvals = _world(away=False)
	if interactive is not None:
		sessions.record_session_start("s1", cwd="C:/Work/X", interactive=interactive)
	route = _build_approvals_route(approvals, None)
	task = asyncio.create_task(route(_request(_hook_input(), asyncio.Event())))
	await _wait_open(approvals, 1)
	await _release_all(approvals, [task])


@pytest.mark.asyncio
async def test_away_a_subagent_and_a_non_interactive_request_are_both_held_and_carded():
	_, sessions, backend, approvals = _world(away=True)
	sessions.record_session_start("s2", cwd="C:/Work/X", interactive=False)
	route = _build_approvals_route(approvals, None)
	tasks = [
		asyncio.create_task(route(_request(_hook_input(agent_id="a1"), asyncio.Event()))),
		asyncio.create_task(route(_request(_hook_input(session_id="s2"), asyncio.Event()))),
	]
	await _wait_open(approvals, 2)
	assert len(backend.cards()) == 2
	await _release_all(approvals, tasks)


@pytest.mark.asyncio
async def test_away_mode_turning_off_releases_what_the_desk_would_not_hold():
	registry, sessions, backend, approvals = _world(away=True)
	sessions.record_session_start("s2", cwd="C:/Work/X", interactive=False)
	sessions.record_session_start("s3", cwd="C:/Work/X", interactive=True)
	route = _build_approvals_route(approvals, None)
	sub = asyncio.create_task(route(_request(_hook_input(agent_id="a1"), asyncio.Event())))
	await _wait_open(approvals, 1)
	headless = asyncio.create_task(route(_request(_hook_input(session_id="s2"), asyncio.Event())))
	await _wait_open(approvals, 2)
	main = asyncio.create_task(route(_request(_hook_input(session_id="s3"), asyncio.Event())))
	await _wait_open(approvals, 3)
	by_session = {(a.cli_session_id, a.agent_id): a for a in approvals.open_approvals()}
	sub_approval = by_session[("s1", "a1")]
	headless_approval = by_session[("s2", None)]
	main_approval = by_session[("s3", None)]
	assert len(backend.cards()) == 3

	registry.update_global_away_cache(False)
	await drain_bg_tasks()

	for task in (sub, headless):
		response = await asyncio.wait_for(task, 2)
		assert response.status_code == 204
	for approval in (sub_approval, headless_approval):
		outcome = approval.future.result()
		assert (outcome.kind, outcome.reason) == ("released", "desk_release")
		assert approval.closed is True
	cancelled_ids = {card_id for _, card_id in backend.cancelled}
	assert len(cancelled_ids) == 3
	assert main_approval.closed is False
	assert main_approval.card_live is False
	assert approvals.open_approvals() == [main_approval]
	assert not main.done()
	await _release_all(approvals, [main])


@pytest.mark.asyncio
async def test_the_session_start_route_stores_interactive_and_ignores_a_non_bool():
	sessions = SessionRegistry()
	route = _build_session_start_route(sessions, _logger())

	async def post(**fields):
		await route(_request({"session_id": "s1", "cwd": "C:/Work/X", **fields}, asyncio.Event()))

	await post(interactive=True)
	assert sessions.get("s1").interactive is True
	await post(interactive=False)
	assert sessions.get("s1").interactive is False
	await post()
	assert sessions.get("s1").interactive is False
	await post(interactive="yes")
	assert sessions.get("s1").interactive is False
	await post(interactive=True)
	assert sessions.get("s1").interactive is True

	fresh = SessionRegistry()
	fresh.hydrate_record({"cli_session_id": "s1", "interactive": False})
	assert fresh.get("s1").interactive is False
	fresh.hydrate_record({"cli_session_id": "s2", "interactive": "x"})
	assert fresh.get("s2").interactive is None
	fresh.hydrate_record({"cli_session_id": "s3"})
	assert fresh.get("s3").interactive is None


async def _pump(n=20):
	for _ in range(n):
		await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_flip_back_on_during_the_card_withdrawal_keeps_the_approval_open():
	registry, sessions, backend, approvals = _world(away=True)
	approval = await approvals.open(
		cli_session_id="s1", agent_id="a1", tool="Bash", tool_input={"command": "ls"}, cwd="C:/Work/X",
	)
	assert approval.card_live is True
	gate = asyncio.Event()
	original = backend.mark_question_cancelled

	async def gated(conversation_id, request_id):
		await gate.wait()
		await original(conversation_id, request_id)

	backend.mark_question_cancelled = gated
	registry.update_global_away_cache(False)
	await _pump()
	registry.update_global_away_cache(True)
	await _pump()
	gate.set()
	await drain_bg_tasks()
	assert approval.closed is False
	assert approval.card_live is True
	assert not approval.future.done()
	await approvals.close(approval, Outcome("released", reason="test"))


@pytest.mark.asyncio
async def test_a_flip_back_on_during_a_release_stops_the_releases_that_follow():
	registry, sessions, backend, approvals = _world(away=True)
	first, second = [
		await approvals.open(
			cli_session_id="s1", agent_id=agent, tool="Bash", tool_input={"command": "ls"}, cwd="C:/Work/X",
		)
		for agent in ("a1", "a2")
	]
	assert first.card_live and second.card_live
	gate = asyncio.Event()
	gated = {"done": False}
	original_info = approvals._logger.info

	async def info(message, *args, **kwargs):
		if "approval_closed" in message and not gated["done"]:
			gated["done"] = True
			await gate.wait()
		return await original_info(message, *args, **kwargs)

	approvals._logger.info = info
	registry.update_global_away_cache(False)
	await _pump()
	assert first.closed is True
	assert second.closed is False
	registry.update_global_away_cache(True)
	await _pump()
	gate.set()
	await drain_bg_tasks()
	assert second.closed is False
	assert not second.future.done()
	assert second.card_live is True
	await approvals.close(second, Outcome("released", reason="test"))
