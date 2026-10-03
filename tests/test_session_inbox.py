"""GET /sessions/{sid}/inbox and POST /sessions/{sid}/stop: the Claude Code
mod's single read route, and the stop it carries."""

import json
import logging

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from server.registry import Registry
from server.session_registry import STOP_FRESH_SECONDS, SessionRegistry
from tests.conftest import make_registry_with_loopback


class _Mono:
	def __init__(self) -> None:
		self.t = 1000.0

	def __call__(self) -> float:
		return self.t


def _app(registry, sessions):
	from server.main import _build_inbox_route, _build_stop_route
	app = Starlette()
	app.add_route("/sessions/{sid}/inbox", _build_inbox_route(registry, sessions), methods=["GET"])
	app.add_route("/sessions/{sid}/stop", _build_stop_route(sessions), methods=["POST"])
	return app


def _sessions(*ids: str, mono=None) -> SessionRegistry:
	sessions = SessionRegistry(mono=mono)
	for sid in ids:
		sessions.record_session_start(sid, cwd="C:/Work/X")
	return sessions


def test_inbox_pops_notices_once():
	sessions = _sessions("s1")
	sessions.queue_notice("s1", "John (from phone): hi")
	with TestClient(_app(Registry(), sessions)) as client:
		first = client.get("/sessions/s1/inbox").json()
		second = client.get("/sessions/s1/inbox").json()
	assert first == {"notices": ["John (from phone): hi"], "stop": False, "away": False, "pending_ask": False}
	assert second["notices"] == []


def test_inbox_reports_global_away_mode():
	registry = Registry()
	registry.global_away_mode = True
	with TestClient(_app(registry, _sessions("s1"))) as client:
		assert client.get("/sessions/s1/inbox").json()["away"] is True


def test_inbox_for_an_unknown_session_has_nothing_but_still_reads_away():
	registry = Registry()
	registry.global_away_mode = True
	with TestClient(_app(registry, _sessions())) as client:
		body = client.get("/sessions/nope/inbox").json()
	assert body == {"notices": [], "stop": False, "away": True, "pending_ask": False}


@pytest.mark.asyncio
async def test_inbox_reports_a_live_blocking_ask():
	from server.main import _build_inbox_route
	registry = make_registry_with_loopback()
	registry.bind_session("sess-live", "conv-1")
	registry.add("conv-1", "sess-live", "claude", "req-1")
	route = _build_inbox_route(registry, _sessions("sess-live"))
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": b"", "path_params": {"sid": "sess-live"}}
	body = json.loads((await route(Request(scope))).body)
	assert body["pending_ask"] is True


def test_stop_is_queued_and_reported_once():
	sessions = _sessions("s1")
	with TestClient(_app(Registry(), sessions)) as client:
		assert client.post("/sessions/s1/stop").json() == {"queued": True}
		assert client.get("/sessions/s1/inbox").json()["stop"] is True
		assert client.get("/sessions/s1/inbox").json()["stop"] is False


def test_stop_for_an_unknown_session_is_not_queued():
	with TestClient(_app(Registry(), _sessions())) as client:
		assert client.post("/sessions/nope/stop").json() == {"queued": False}


def test_a_stop_older_than_the_window_is_dropped():
	mono = _Mono()
	sessions = _sessions("s1", mono=mono)
	assert sessions.request_stop("s1") is True
	mono.t += STOP_FRESH_SECONDS + 1
	assert sessions.take_stop("s1") is False
	assert sessions.take_stop("s1") is False


def test_a_stop_inside_the_window_is_taken():
	mono = _Mono()
	sessions = _sessions("s1", mono=mono)
	sessions.request_stop("s1")
	mono.t += STOP_FRESH_SECONDS - 1
	assert sessions.take_stop("s1") is True


def test_a_stop_is_never_part_of_the_persisted_record():
	sessions = _sessions("s1")
	sessions.request_stop("s1")
	assert not any("stop" in key for key in sessions.get("s1").to_payload())


def test_the_inbox_is_behind_the_bearer_gate_for_a_non_loopback_peer():
	from server.http_auth import TokenAuthMiddleware
	app = TokenAuthMiddleware(_app(Registry(), _sessions("s1")), token="tok")
	with TestClient(app) as client:
		assert client.get("/sessions/s1/inbox").status_code == 401
		assert client.get("/sessions/s1/inbox", headers={"Authorization": "Bearer tok"}).status_code == 200


def _access_record(path: str) -> logging.LogRecord:
	return logging.LogRecord(
		"uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
		("127.0.0.1:50000", "GET", path, "1.1", 200), None,
	)


def test_the_access_log_filter_drops_inbox_lines_only():
	from server.main import _DropInboxAccessLines
	drop = _DropInboxAccessLines()
	assert drop.filter(_access_record("/sessions/s1/inbox")) is False
	assert drop.filter(_access_record("/agent_status")) is True
