"""Tests for the GET /stats endpoint (widget-facing roll-up)."""

import json

import pytest

from server.main import _build_stats_route
from server.registry import Conversation, Registry
from server.session_registry import SessionRegistry


def _make_request() -> "Request":
	from starlette.requests import Request
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": b""}
	return Request(scope)


class _FakeLoopSup:
	"""Minimal stand-in for LoopSupervisor: health() returns an object with
	a consecutive_failures attribute, matching the real supervisor's contract."""

	def __init__(self, consecutive_failures: int) -> None:
		self._cf = consecutive_failures

	def health(self):
		class _H:
			pass
		h = _H()
		h.consecutive_failures = self._cf
		return h


class _FakeBackend:
	def __init__(self, listeners) -> None:
		self._listeners = listeners

	def listener_health(self):
		return list(self._listeners)


def _registry(active=0, ended=0, away=False):
	r = Registry()
	for i in range(active):
		r.conversations[f"a{i}"] = Conversation(id=f"a{i}", title="a", state="active")
	for i in range(ended):
		r.conversations[f"e{i}"] = Conversation(id=f"e{i}", title="e", state="ended")
	r.update_global_away_cache(away)
	return r


@pytest.mark.asyncio
async def test_stats_returns_all_keys_and_counts():
	registry = _registry(active=3, ended=2, away=True)
	registry.add(conversation_id="a0", cli_session_id="s-a0", sender="Claude", request_id="r1")
	registry.add(conversation_id="a1", cli_session_id="s-a1", sender="Gemini", request_id="r2")
	backend = _FakeBackend([{"name": "responses", "state": "live"}])
	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}
	route = _build_stats_route(registry, backend, loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)
	assert set(body.keys()) == {
		"active_conversations", "pending_count",
		"oldest_pending_age_seconds", "away_mode", "healthy", "sessions",
	}
	assert body["active_conversations"] == 3
	assert body["pending_count"] == 2
	assert body["away_mode"] is True
	assert body["oldest_pending_age_seconds"] is not None
	assert body["healthy"] is True


@pytest.mark.asyncio
async def test_stats_empty_registry_oldest_is_null_and_healthy():
	registry = _registry()
	backend = _FakeBackend([])
	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}
	route = _build_stats_route(registry, backend, loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)
	assert body["active_conversations"] == 0
	assert body["pending_count"] == 0
	assert body["oldest_pending_age_seconds"] is None
	assert body["away_mode"] is False
	assert body["healthy"] is True


@pytest.mark.asyncio
async def test_stats_unhealthy_when_listener_reconnecting():
	registry = _registry(active=1)
	backend = _FakeBackend([{"name": "responses", "state": "reconnecting"}])
	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}
	route = _build_stats_route(registry, backend, loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)
	assert body["healthy"] is False


@pytest.mark.asyncio
async def test_stats_healthy_when_listener_stopped_or_starting():
	"""'stopped' is an intentional shutdown and 'starting' is transient; only
	'reconnecting' counts as unhealthy, matching the dashboard's rollUpHealth."""
	registry = _registry(active=1)
	backend = _FakeBackend([
		{"name": "responses", "state": "stopped"},
		{"name": "away_mode_global", "state": "starting"},
	])
	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}
	route = _build_stats_route(registry, backend, loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)
	assert body["healthy"] is True


@pytest.mark.asyncio
async def test_stats_unhealthy_when_loop_has_consecutive_failures():
	registry = _registry(active=1)
	backend = _FakeBackend([{"name": "responses", "state": "live"}])
	loop_sups = {"dispatch_responses": _FakeLoopSup(2)}
	route = _build_stats_route(registry, backend, loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)
	assert body["healthy"] is False


@pytest.mark.asyncio
async def test_stats_listener_health_missing_treated_as_healthy():
	"""A backend without a listener_health method (or one that raises) must not
	500 the widget; absence of listener data rolls up as no-reconnecting-listener."""
	registry = _registry(active=1)

	class _NoHealthBackend:
		pass

	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}
	route = _build_stats_route(registry, _NoHealthBackend(), loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)
	assert body["healthy"] is True


@pytest.mark.asyncio
async def test_stats_includes_sessions_block_with_counts_by_state():
	registry = _registry(active=1)
	backend = _FakeBackend([{"name": "responses", "state": "live"}])
	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}
	session_registry = SessionRegistry()
	session_registry.record_session_start("sess-1", cwd="c:/work/sw")
	session_registry.upsert_from_hook("sess-2", state="active")
	session_registry.record_session_end("sess-2", reason="hook_sessionend", ended_at="t1")

	route = _build_stats_route(registry, backend, loop_sups, session_registry=session_registry)
	resp = await route(_make_request())
	body = json.loads(resp.body)

	assert body["sessions"] == {"total": 2, "by_state": {"idle": 1, "ended": 1}}


@pytest.mark.asyncio
async def test_stats_sessions_block_defaults_when_session_registry_is_none():
	registry = _registry(active=1)
	backend = _FakeBackend([{"name": "responses", "state": "live"}])
	loop_sups = {"dispatch_responses": _FakeLoopSup(0)}

	route = _build_stats_route(registry, backend, loop_sups)
	resp = await route(_make_request())
	body = json.loads(resp.body)

	assert body["sessions"] == {"total": 0, "by_state": {}}
