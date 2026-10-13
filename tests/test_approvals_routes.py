"""POST /approvals (the PermissionRequest hook's long-poll) and
POST /sessions/{sid}/approvals/settled, driven through their route functions
with a hand-built ASGI receive, so a test can drop the connection."""

import asyncio
import json

import pytest
from starlette.requests import Request

from server.approvals import ApprovalRegistry
from server.main import _build_approvals_route, _build_approvals_settled_route
from server.registry import Registry
from server.service_stop import ServiceStop
from server.session_registry import SessionRegistry
from tests.test_approvals import _CardBackend, _logger, _resolve


def _request(body, disconnect: asyncio.Event, path_params=None) -> Request:
	payload = body if isinstance(body, bytes) else json.dumps(body).encode()
	delivered = False

	async def receive():
		nonlocal delivered
		if not delivered:
			delivered = True
			return {"type": "http.request", "body": payload, "more_body": False}
		await disconnect.wait()
		return {"type": "http.disconnect"}

	scope = {
		"type": "http", "method": "POST", "path": "/approvals",
		"headers": [(b"content-type", b"application/json")], "query_string": b"",
		"path_params": path_params or {},
	}
	return Request(scope, receive)


def _hook_input(**overrides):
	body = {
		"session_id": "s1", "cwd": "C:/Work/X", "hook_event_name": "PermissionRequest", "permission_mode": "default",
		"tool_name": "Bash", "tool_input": {"command": "ls", "description": "list"},
	}
	body.update(overrides)
	return body


def _approvals(away=False):
	registry = Registry()
	registry.update_global_away_cache(away)
	backend = _CardBackend()
	return ApprovalRegistry(registry, SessionRegistry(), backend, _logger(), resolve_conversation=_resolve), backend


async def _held(approvals, route, request):
	task = asyncio.create_task(route(request))
	for _ in range(200):
		if approvals.open_approvals():
			break
		await asyncio.sleep(0.005)
	(approval,) = approvals.open_approvals()
	return task, approval


@pytest.mark.asyncio
async def test_an_answer_from_the_phone_returns_the_hook_decision():
	approvals, _ = _approvals(away=True)
	route = _build_approvals_route(approvals, ServiceStop())
	task, approval = await _held(approvals, route, _request(_hook_input(), asyncio.Event()))
	await approvals.answer("conv-1", approval.card_id, "Allow", None)
	response = await asyncio.wait_for(task, 2)
	assert response.status_code == 200
	assert json.loads(response.body) == {
		"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}},
	}


@pytest.mark.asyncio
async def test_a_terminal_answer_releases_the_hook_with_no_decision():
	approvals, _ = _approvals()
	route = _build_approvals_route(approvals, ServiceStop())
	task, _approval = await _held(approvals, route, _request(_hook_input(), asyncio.Event()))
	await approvals.settle_from_terminal(
		"s1", agent_id=None, tool="Bash", tool_input={"command": "ls", "description": "list"},
		decision="accept", source="user_temporary",
	)
	response = await asyncio.wait_for(task, 2)
	assert response.status_code == 204
	assert response.body == b""


@pytest.mark.asyncio
async def test_a_hook_that_goes_away_closes_its_approval():
	approvals, backend = _approvals(away=True)
	route = _build_approvals_route(approvals, ServiceStop())
	gone = asyncio.Event()
	task, approval = await _held(approvals, route, _request(_hook_input(), gone))
	card_id = approval.card_id
	gone.set()
	await asyncio.wait_for(task, 2)
	assert approval.future.result().reason == "hook_gone"
	assert backend.cancelled == [("conv-1", card_id)]
	assert approvals.open_approvals() == []


@pytest.mark.asyncio
async def test_a_service_stop_answers_503_and_leaves_the_card_for_hydration():
	approvals, backend = _approvals(away=True)
	stop = ServiceStop()
	route = _build_approvals_route(approvals, stop)
	task, approval = await _held(approvals, route, _request(_hook_input(), asyncio.Event()))
	stop.raise_()
	response = await asyncio.wait_for(task, 2)
	assert response.status_code == 503
	assert approval.future.result().reason == "service_stop"
	assert backend.cancelled == []


@pytest.mark.asyncio
async def test_a_stop_that_also_disconnects_the_hook_still_answers_503():
	"""uvicorn's shutdown wakes receive() with a disconnect right after the stop
	flag goes up, before the poll fires; the flag decides, not the disconnect."""
	approvals, backend = _approvals(away=True)
	stop = ServiceStop()
	route = _build_approvals_route(approvals, stop)
	disconnect = asyncio.Event()
	task, approval = await _held(approvals, route, _request(_hook_input(), disconnect))
	stop.raise_()
	disconnect.set()
	response = await asyncio.wait_for(task, 2)
	assert response.status_code == 503
	assert approval.future.result().reason == "service_stop"
	assert backend.cancelled == []


@pytest.mark.asyncio
async def test_a_hook_arriving_during_a_stop_gets_503_and_no_approval():
	registry = Registry()
	logger = _logger()
	approvals = ApprovalRegistry(registry, SessionRegistry(), _CardBackend(), logger, resolve_conversation=_resolve)
	stop = ServiceStop()
	stop.raise_()
	route = _build_approvals_route(approvals, stop)
	response = await asyncio.wait_for(route(_request(_hook_input(), asyncio.Event())), 2)
	assert response.status_code == 503
	assert approvals.open_approvals() == []
	logged = [call.args[0] for call in logger.info.await_args_list if call.args]
	assert not any(str(line).startswith("approval_opened") for line in logged)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode"])
async def test_excluded_tools_are_left_to_the_terminal_at_once(tool):
	approvals, _ = _approvals(away=True)
	route = _build_approvals_route(approvals, ServiceStop())
	response = await asyncio.wait_for(route(_request(_hook_input(tool_name=tool), asyncio.Event())), 2)
	assert response.status_code == 204
	assert approvals.open_approvals() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", [1, 2], {"tool_name": "Bash"}, {"session_id": "s1"}])
async def test_a_malformed_body_gets_204(body):
	approvals, _ = _approvals()
	route = _build_approvals_route(approvals, ServiceStop())
	response = await asyncio.wait_for(route(_request(body, asyncio.Event())), 2)
	assert response.status_code == 204
	assert approvals.open_approvals() == []


@pytest.mark.asyncio
async def test_a_subagent_hook_records_its_agent():
	approvals, _ = _approvals(away=True)
	route = _build_approvals_route(approvals, ServiceStop())
	task, approval = await _held(approvals, route, _request(_hook_input(agent_id="agent-1"), asyncio.Event()))
	assert approval.agent_id == "agent-1"
	await approvals.close_session("s1", "session_end")
	assert (await asyncio.wait_for(task, 2)).status_code == 204


@pytest.mark.asyncio
async def test_the_settled_route_releases_the_matching_approval():
	approvals, _ = _approvals()
	route = _build_approvals_route(approvals, ServiceStop())
	task, approval = await _held(approvals, route, _request(_hook_input(), asyncio.Event()))
	settled = _build_approvals_settled_route(approvals)
	report = {
		"tool_use_id": "toolu_1", "tool": "Bash", "agent_id": None,
		"input": {"command": "ls", "description": "list"}, "decision": "accept", "source": "user_temporary",
	}
	response = await settled(_request(report, asyncio.Event(), path_params={"sid": "s1"}))
	assert response.status_code == 200
	assert (await asyncio.wait_for(task, 2)).status_code == 204
	assert approval.future.result().reason == "terminal:user_temporary:accept"


@pytest.mark.asyncio
async def test_the_settled_route_ignores_a_malformed_report():
	approvals, _ = _approvals()
	settled = _build_approvals_settled_route(approvals)
	for body in (b"nope", {"input": {}}, {"tool": ""}):
		response = await settled(_request(body, asyncio.Event(), path_params={"sid": "s1"}))
		assert response.status_code == 200
