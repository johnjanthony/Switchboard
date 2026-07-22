"""Tests for the GET /stats endpoint (widget-facing roll-up)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from server.main import _build_stats_route, _compute_healthy
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
		"oldest_pending_age_seconds", "away_mode", "healthy", "sessions", "needs_you",
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
	'reconnecting' counts as unhealthy, matching _compute_healthy."""
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


def test_compute_healthy_true_when_no_reconnecting_and_no_failures():
	assert _compute_healthy([{"state": "live"}], [0]) is True


def test_compute_healthy_false_when_a_listener_is_reconnecting():
	assert _compute_healthy([{"state": "reconnecting"}], [0]) is False


def test_compute_healthy_true_for_stopped_or_starting_listeners():
	assert _compute_healthy([{"state": "stopped"}, {"state": "starting"}], [0]) is True


def test_compute_healthy_false_when_a_loop_has_consecutive_failures():
	assert _compute_healthy([{"state": "live"}], [0, 2]) is False


def test_compute_healthy_true_on_empty_inputs():
	assert _compute_healthy([], []) is True


def test_compute_healthy_ignores_non_dict_listener_entries():
	assert _compute_healthy([None, {"state": "live"}], [0]) is True


@pytest.mark.asyncio
async def test_stats_needs_you_live_pending_is_ask():
	registry = _registry(active=1)
	registry.add(conversation_id="a0", cli_session_id="s-live", sender="Claude", request_id="r1")
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)})
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s-live"]["reason"] == "ask"
	assert 0.0 <= body["needs_you"]["s-live"]["age_seconds"] < 60.0


@pytest.mark.asyncio
async def test_stats_needs_you_includes_parked_pending():
	registry = _registry(active=1)
	registry.add_parked(conversation_id="a0", cli_session_id="s-parked", sender="Claude", request_id="r1",
		started_at=datetime.now(timezone.utc) - timedelta(seconds=300))
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)})
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s-parked"]["reason"] == "ask"
	assert body["needs_you"]["s-parked"]["age_seconds"] >= 300.0


@pytest.mark.asyncio
async def test_stats_needs_you_oldest_age_wins_across_conversations():
	registry = _registry(active=2)
	registry.add(conversation_id="a0", cli_session_id="s-x", sender="Claude", request_id="r1")
	registry.add_parked(conversation_id="a1", cli_session_id="s-x", sender="Claude", request_id="r2",
		started_at=datetime.now(timezone.utc) - timedelta(seconds=500))
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)})
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s-x"]["age_seconds"] >= 500.0


@pytest.mark.asyncio
async def test_stats_needs_you_blocked_on_approval():
	registry = _registry()
	sreg = SessionRegistry()
	sreg.upsert_from_hook("s-appr", state="active", event="PreToolUse", in_tool=True)
	sreg.apply_rings({"s-appr": {"title_state": "star"}})
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)}, session_registry=sreg)
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s-appr"]["reason"] == "approval"
	assert body["needs_you"]["s-appr"]["age_seconds"] >= 0.0


@pytest.mark.asyncio
async def test_stats_needs_you_excludes_terminal_approval_records():
	registry = _registry()
	sreg = SessionRegistry()
	sreg.upsert_from_hook("s-lost", state="active", event="PreToolUse", in_tool=True)
	sreg.apply_rings({"s-lost": {"title_state": "star"}})
	rec = sreg.get("s-lost")
	rec.state = "lost"  # belt test: terminal transitions normally clear the flag; force the stale combination
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)}, session_registry=sreg)
	body = json.loads((await route(_make_request())).body)
	assert "s-lost" not in body["needs_you"]


@pytest.mark.asyncio
async def test_stats_needs_you_ask_wins_over_approval():
	registry = _registry(active=1)
	registry.add(conversation_id="a0", cli_session_id="s-both", sender="Claude", request_id="r1")
	sreg = SessionRegistry()
	sreg.upsert_from_hook("s-both", state="active", event="PreToolUse", in_tool=True)
	sreg.apply_rings({"s-both": {"title_state": "star"}})
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)}, session_registry=sreg)
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"]["s-both"]["reason"] == "ask"


@pytest.mark.asyncio
async def test_stats_needs_you_empty_when_nothing_pending():
	registry = _registry(active=1)
	route = _build_stats_route(registry, _FakeBackend([]), {"d": _FakeLoopSup(0)})
	body = json.loads((await route(_make_request())).body)
	assert body["needs_you"] == {}
