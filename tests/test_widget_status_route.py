"""Tests for POST /widget-status control route."""

import json

import pytest

from server.main import _build_widget_status_route


def _request(action=None, raw=None):
	from starlette.requests import Request
	payload = raw if raw is not None else json.dumps({"action": action}).encode()

	async def receive():
		return {"type": "http.request", "body": payload, "more_body": False}

	scope = {"type": "http", "method": "POST", "headers": [], "query_string": b""}
	return Request(scope, receive)


class _FakeService:
	def __init__(self):
		self.calls = []

	async def check(self):
		self.calls.append("check")
		return {"watch_state": "watching", "button": "stop", "level": "major"}

	async def stop(self):
		self.calls.append("stop")
		return {"watch_state": "idle", "button": "check", "level": "operational"}


@pytest.mark.asyncio
async def test_check_action_invokes_service_check():
	svc = _FakeService()
	route = _build_widget_status_route(svc)
	resp = await route(_request("check"))
	out = json.loads(resp.body)
	assert resp.status_code == 200
	assert svc.calls == ["check"]
	assert out["watch_state"] == "watching"


@pytest.mark.asyncio
async def test_stop_action_invokes_service_stop():
	svc = _FakeService()
	route = _build_widget_status_route(svc)
	resp = await route(_request("stop"))
	assert json.loads(resp.body)["watch_state"] == "idle"
	assert svc.calls == ["stop"]


@pytest.mark.asyncio
async def test_unknown_action_returns_400():
	svc = _FakeService()
	route = _build_widget_status_route(svc)
	resp = await route(_request("frobnicate"))
	assert resp.status_code == 400
	assert svc.calls == []


@pytest.mark.asyncio
async def test_malformed_body_returns_400():
	svc = _FakeService()
	route = _build_widget_status_route(svc)
	resp = await route(_request(raw=b"not json"))
	assert resp.status_code == 400
