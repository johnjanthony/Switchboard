"""Tests for Starlette routes in main.py."""

import asyncio
import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from server.main import (
	_build_away_mode_route,
)
from server.registry import Registry
from tests.conftest import make_registry_with_loopback

_CWD = "c:/work/sw"


def _make_request(cwd: str = "") -> Request:
	qs = f"cwd={cwd}".encode() if cwd else b""
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": qs}
	return Request(scope)


def _make_session_request(session_id: str) -> Request:
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": f"session_id={session_id}".encode()}
	return Request(scope)


@pytest.mark.asyncio
async def test_away_mode_route_returns_global_flag_invariant_of_cwd(tmp_path):
	"""Post-conversations-redesign the route returns the global flag regardless
	of any `?cwd=` query (which is accepted but ignored for back-compat with
	older hook deployments). No `session_id` is supplied here, so notices is
	always the empty list."""
	from server.session_registry import SessionRegistry

	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	session_registry = SessionRegistry()
	route = _build_away_mode_route(registry, session_registry)

	# No cwd → reflects global flag
	resp = await route(_make_request())
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": True, "notices": [], "pending_ask": False}

	# Garbage cwd → still reflects global flag (legacy clients send these;
	# server ignores)
	resp = await route(_make_request("relative/path"))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": True, "notices": [], "pending_ask": False}

	# Flag flips → response flips
	registry.update_global_away_cache(False)
	resp = await route(_make_request("c:/work/sw"))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False, "notices": [], "pending_ask": False}


# --- Backgrounded-ask handback: pending_ask in the /away-mode response ---

@pytest.mark.asyncio
async def test_away_mode_route_reports_live_blocking_pending():
	"""A session whose conversation holds a live blocking ask gets pending_ask=true.
	The turn-end hook treats that live ask as the agent's handback: the harness
	backgrounds MCP calls that outlive ~120s, so a turn can legitimately end
	while its ask_human is still awaiting John."""
	from server.session_registry import SessionRegistry

	registry = make_registry_with_loopback()
	registry.bind_session("sess-live", "conv-1")
	registry.add("conv-1", "sess-live", "claude", "req-1")
	route = _build_away_mode_route(registry, SessionRegistry())

	resp = await route(_make_session_request("sess-live"))
	assert resp.status_code == 200
	body = json.loads(resp.body)
	assert body["active"] is True
	assert body["pending_ask"] is True


@pytest.mark.asyncio
async def test_away_mode_route_parked_pending_is_not_a_handback():
	"""A parked (future-less) pending means the client-side call is gone - the
	agent must re-ask, so pending_ask stays false."""
	from server.session_registry import SessionRegistry

	registry = make_registry_with_loopback()
	registry.bind_session("sess-parked", "conv-1")
	registry.add("conv-1", "sess-parked", "claude", "req-1")
	registry.find_by_request_id("conv-1", "req-1").future = None
	route = _build_away_mode_route(registry, SessionRegistry())

	resp = await route(_make_session_request("sess-parked"))
	body = json.loads(resp.body)
	assert body["pending_ask"] is False


@pytest.mark.asyncio
async def test_away_mode_route_background_ask_is_not_a_handback():
	"""A background ask deliberately does not satisfy the turn-end hook."""
	from server.session_registry import SessionRegistry

	registry = make_registry_with_loopback()
	registry.bind_session("sess-bg", "conv-1")
	registry.add_background("conv-1", "sess-bg", "claude", "req-bg", question="q")
	route = _build_away_mode_route(registry, SessionRegistry())

	resp = await route(_make_session_request("sess-bg"))
	body = json.loads(resp.body)
	assert body["pending_ask"] is False


@pytest.mark.asyncio
async def test_away_mode_route_unbound_session_pending_ask_false():
	from server.session_registry import SessionRegistry

	registry = make_registry_with_loopback()
	route = _build_away_mode_route(registry, SessionRegistry())

	resp = await route(_make_session_request("sess-unknown"))
	body = json.loads(resp.body)
	assert body["pending_ask"] is False


# --- T7 / H9: /collab-partner-state route ---

# --- /healthz extended payload ---

@pytest.mark.asyncio
async def test_healthz_returns_pending_listeners_and_dispatch_loops(tmp_path, monkeypatch):
	"""/healthz must include all three top-level keys after Tasks 13/14."""
	from server.firebase_supervisor import LoopSupervisor
	from server.logging_jsonl import JsonlLogger

	logger = JsonlLogger(tmp_path / "test.jsonl")
	registry = Registry()

	class _BackendWithHealth:
		async def send_text(self, _msg): pass
		def listener_health(self):
			return [
				{"name": "responses", "state": "live", "last_event_seconds_ago": 0.5,
				 "crash_count": 0, "last_crash_seconds_ago": None},
			]

	backend = _BackendWithHealth()
	loop_sups = {
		"dispatch_responses": LoopSupervisor("dispatch_responses", backend, logger.surface_error),
	}
	# Inline the healthz route construction (mirrors main.py:_run). If
	# main.py grows a factory, switch this to import the factory.
	async def _route(request):
		import time as _time
		listeners = backend.listener_health()
		now = _time.monotonic()
		dispatch_loops = []
		for sup in loop_sups.values():
			h = sup.health()
			dispatch_loops.append({
				"name": h.name,
				"consecutive_failures": h.consecutive_failures,
				"crash_count": h.crash_count,
				"last_crash_seconds_ago": (
					(now - h.last_crash_at) if h.last_crash_at is not None else None
				),
			})
		dispatch_loops.sort(key=lambda d: d["name"])
		return JSONResponse({
			"pending": {
				"count": registry.pending_count,
				"parked": registry.parked_count,
				"oldest_pending_age_seconds": registry.oldest_pending_age_seconds,
				"total_answered": registry.total_answered,
			},
			"listeners": listeners,
			"dispatch_loops": dispatch_loops,
		})

	resp = await _route(_make_request())
	body = json.loads(resp.body)
	assert "pending" in body
	assert "listeners" in body
	assert "dispatch_loops" in body
	assert len(body["dispatch_loops"]) == 1
	assert {d["name"] for d in body["dispatch_loops"]} == {
		"dispatch_responses",
	}
	assert body["listeners"][0]["state"] == "live"
	assert body["pending"]["count"] == 0
	assert body["pending"]["parked"] == 0
