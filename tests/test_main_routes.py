"""Tests for Starlette routes in main.py."""

import asyncio
import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from server.main import (
	_build_away_mode_route,
	_build_collab_partner_state_route,
	_wire_away_mode_mirror,
)
from server.collab import CollabSession
from server.registry import Registry
from tests.conftest import make_registry_with_loopback

_CWD = "c:/work/sw"


def _make_request(cwd: str = "") -> Request:
	qs = f"cwd={cwd}".encode() if cwd else b""
	scope = {"type": "http", "method": "GET", "headers": [], "query_string": qs}
	return Request(scope)


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_by_default(tmp_path):
	registry = Registry()
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_when_no_cwd_param(tmp_path):
	"""No cwd query param → False (fail-open)."""
	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request())  # no cwd
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_returns_true_when_global_away_set(tmp_path):
	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"active": True}


@pytest.mark.asyncio
async def test_away_mode_route_returns_true_when_cwd_override_set(tmp_path):
	registry = make_registry_with_loopback()
	registry.update_cwd_override_cache(_CWD, True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"active": True}


@pytest.mark.asyncio
async def test_away_mode_route_returns_false_on_invalid_cwd(tmp_path):
	"""Non-absolute cwd fails canonicalization → returns False."""
	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request("relative/path"))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"active": False}


@pytest.mark.asyncio
async def test_away_mode_route_cwd_override_false_overrides_global(tmp_path):
	"""Per-cwd override False takes precedence over global True."""
	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	registry.update_cwd_override_cache(_CWD, False)
	route = _build_away_mode_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"active": False}


class _MirrorBackend:
	def __init__(self):
		self.calls: list[tuple] = []

	async def write_away_mode_mirror(self, cwd: str | None, active: bool | None) -> None:
		self.calls.append((cwd, active))


@pytest.mark.asyncio
async def test_wire_away_mode_mirror_pushes_on_toggle(tmp_path):
	"""The registry callback (triggered by set_cwd_override or remove_cwd_override)
	forwards the write to the backend."""
	registry = Registry()
	backend = _MirrorBackend()
	await _wire_away_mode_mirror(registry, backend)

	# No initial push — Firebase is the source of truth.
	assert backend.calls == []

	# Per-cwd toggle fires callback
	registry.set_cwd_override(_CWD, True)
	await asyncio.sleep(0.01)  # let _spawn_bg task run
	assert backend.calls == [(_CWD, True)]

	# Mimic listener fire so it's in the cache for the remove call
	registry.update_cwd_override_cache(_CWD, True)

	# remove_cwd_override fires callback with None
	registry.remove_cwd_override(_CWD)
	await asyncio.sleep(0.01)  # let _spawn_bg task run
	assert backend.calls == [(_CWD, True), (None if _CWD is None else _CWD, None)]


@pytest.mark.asyncio
async def test_wire_away_mode_mirror_pushes_on_cwd_override(tmp_path):
	"""Per-cwd override fires the mirror callback with the cwd value."""
	registry = Registry()
	backend = _MirrorBackend()
	await _wire_away_mode_mirror(registry, backend)

	assert backend.calls == []

	registry.set_cwd_override(_CWD, True)
	await asyncio.sleep(0.01)
	# Per-cwd override: mirror called with (cwd, True)
	assert backend.calls == [(_CWD, True)]


# --- T7 / H9: /collab-partner-state route ---

@pytest.mark.asyncio
async def test_collab_partner_state_returns_none_when_no_session():
	registry = Registry()
	route = _build_collab_partner_state_route(registry)
	resp = await route(_make_request(_CWD))
	assert resp.status_code == 200
	assert json.loads(resp.body) == {"state": "none"}


@pytest.mark.asyncio
async def test_collab_partner_state_returns_none_when_no_cwd_param():
	registry = Registry()
	route = _build_collab_partner_state_route(registry)
	resp = await route(_make_request(""))  # no cwd
	assert json.loads(resp.body) == {"state": "none"}


@pytest.mark.asyncio
async def test_collab_partner_state_returns_none_when_invalid_cwd():
	registry = Registry()
	route = _build_collab_partner_state_route(registry)
	resp = await route(_make_request("relative/path"))
	assert json.loads(resp.body) == {"state": "none"}


@pytest.mark.asyncio
async def test_collab_partner_state_returns_none_when_session_has_one_member():
	"""BYO opening: only one agent has enrolled. No partner exists yet to be
	'blocked' so the route returns 'none' to keep the hook permissive."""
	registry = Registry()
	session = CollabSession(cwd=_CWD, agent_senders=["Alice"], task="")
	registry.add_session(session)
	route = _build_collab_partner_state_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"state": "none"}


@pytest.mark.asyncio
async def test_collab_partner_state_returns_live_when_no_one_blocked():
	"""Both enrolled, neither in `_waiting`. Steady state during active work."""
	registry = Registry()
	session = CollabSession(cwd=_CWD, agent_senders=["Alice", "Bob"], task="")
	registry.add_session(session)
	route = _build_collab_partner_state_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"state": "live"}


@pytest.mark.asyncio
async def test_collab_partner_state_returns_blocked_when_a_partner_is_waiting():
	"""Partner is parked in `_waiting` awaiting a message — the firing agent
	is the live one and shouldn't end its turn silently."""
	registry = Registry()
	session = CollabSession(cwd=_CWD, agent_senders=["Alice", "Bob"], task="")
	registry.add_session(session)
	# Simulate Bob blocked in start_waiting.
	loop = asyncio.get_running_loop()
	bob_future = loop.create_future()
	session._waiting["Bob"] = bob_future
	route = _build_collab_partner_state_route(registry)
	resp = await route(_make_request(_CWD))
	assert json.loads(resp.body) == {"state": "blocked"}


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
		"dispatch_commands": LoopSupervisor("dispatch_commands", backend, logger.surface_error),
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
	assert len(body["dispatch_loops"]) == 2
	assert {d["name"] for d in body["dispatch_loops"]} == {
		"dispatch_responses", "dispatch_commands",
	}
	assert body["listeners"][0]["state"] == "live"
	assert body["pending"]["count"] == 0
