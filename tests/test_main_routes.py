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
	older hook deployments). The route never pops notices, so notices is
	always the empty list."""
	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	route = _build_away_mode_route(registry)

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
	registry = make_registry_with_loopback()
	registry.bind_session("sess-live", "conv-1")
	registry.add("conv-1", "sess-live", "claude", "req-1")
	route = _build_away_mode_route(registry)

	resp = await route(_make_session_request("sess-live"))
	assert resp.status_code == 200
	body = json.loads(resp.body)
	assert body["active"] is True
	assert body["pending_ask"] is True


@pytest.mark.asyncio
async def test_away_mode_route_parked_pending_is_not_a_handback():
	"""A parked (future-less) pending means the client-side call is gone - the
	agent must re-ask, so pending_ask stays false."""
	registry = make_registry_with_loopback()
	registry.bind_session("sess-parked", "conv-1")
	registry.add("conv-1", "sess-parked", "claude", "req-1")
	registry.find_by_request_id("conv-1", "req-1").future = None
	route = _build_away_mode_route(registry)

	resp = await route(_make_session_request("sess-parked"))
	body = json.loads(resp.body)
	assert body["pending_ask"] is False


@pytest.mark.asyncio
async def test_away_mode_route_background_ask_is_not_a_handback():
	"""A background ask deliberately does not satisfy the turn-end hook."""
	registry = make_registry_with_loopback()
	registry.bind_session("sess-bg", "conv-1")
	registry.add_background("conv-1", "sess-bg", "claude", "req-bg", question="q")
	route = _build_away_mode_route(registry)

	resp = await route(_make_session_request("sess-bg"))
	body = json.loads(resp.body)
	assert body["pending_ask"] is False


@pytest.mark.asyncio
async def test_away_mode_route_unbound_session_pending_ask_false():
	registry = make_registry_with_loopback()
	route = _build_away_mode_route(registry)

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


# -- a lost-dormant member returns with its session, or stays dormant on a new life --

from unittest.mock import AsyncMock, MagicMock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.approvals import ApprovalRegistry
from server.member_lifecycle import LOST_REASON, lost_message, make_member_dormant, sweep_lost_members
from server.quota_store import QuotaStore
from tests.test_cli_session_end import _fixed_now
from tests.test_member_lifecycle import _Log, _bound_live_member, _pump


async def _dormant_world(backend=None):
	registry, fake, conv_id, sid, sessions, clock = _bound_live_member()
	backend = backend or fake
	await make_member_dormant(
		registry, sid, reason=LOST_REASON, message_for=lambda m: lost_message(m.sender), now=_fixed_now, backend=backend,
	)
	await _pump()
	return registry, backend, conv_id, sid, sessions


def _assert_revived(registry, conv_id, sid):
	member = registry.conversations[conv_id].members_active[sid]
	assert member.alive is True
	assert registry.session_to_conversation_id[sid] == conv_id


@pytest.mark.asyncio
async def test_an_inbox_poll_revives_a_lost_dormant_member():
	from server.main import _build_inbox_route
	registry, backend, conv_id, sid, sessions = await _dormant_world()
	app = Starlette()
	app.add_route("/sessions/{sid}/inbox", _build_inbox_route(registry, sessions, backend, _Log()), methods=["GET"])
	assert TestClient(app).get(f"/sessions/{sid}/inbox").status_code == 200
	_assert_revived(registry, conv_id, sid)


@pytest.mark.asyncio
async def test_a_status_post_revives():
	from server.main import _build_agent_status_route
	registry, backend, conv_id, sid, sessions = await _dormant_world()
	handlers = MagicMock()
	handlers.handle_agent_status = AsyncMock()
	app = Starlette()
	app.add_route(
		"/agent_status",
		_build_agent_status_route(handlers, sessions, registry=registry, backend=backend, logger=_Log()),
		methods=["POST"],
	)
	resp = TestClient(app).post("/agent_status", json={"session_id": sid, "state": "thinking", "event": "UserPromptSubmit"})
	assert resp.status_code == 200
	_assert_revived(registry, conv_id, sid)


@pytest.mark.asyncio
async def test_a_telemetry_post_revives():
	from server.main import _build_telemetry_route
	registry, backend, conv_id, sid, sessions = await _dormant_world()
	app = Starlette()
	app.add_route(
		"/sessions/{sid}/telemetry",
		_build_telemetry_route(sessions, QuotaStore(), backend, _Log(), registry),
		methods=["POST"],
	)
	resp = TestClient(app).post(f"/sessions/{sid}/telemetry", json={"model": "claude-opus-4-7"})
	assert resp.status_code == 200
	_assert_revived(registry, conv_id, sid)


@pytest.mark.asyncio
async def test_session_start_closes_the_window_and_does_not_revive():
	from server.main import _build_session_start_route
	registry, backend, conv_id, sid, sessions = await _dormant_world()
	app = Starlette()
	app.add_route(
		"/session_start", _build_session_start_route(sessions, _Log(), registry=registry, backend=backend), methods=["POST"],
	)
	assert TestClient(app).post("/session_start", json={"session_id": sid, "cwd": "C:/X"}).status_code == 200
	member = registry.conversations[conv_id].members_active[sid]
	assert member.alive is False and member.session_end_reason == "restarted"
	assert sid not in registry.session_to_conversation_id


@pytest.mark.asyncio
async def test_a_tool_call_that_beats_its_status_post_revives_and_does_not_mint(tmp_path):
	from server.config import Config
	from server.gateway import build_tool_handlers
	from server.logging_jsonl import JsonlLogger
	from tests.test_gateway_notify_human import RecordingBackend
	backend = RecordingBackend()
	registry, backend, conv_id, sid, sessions = await _dormant_world(backend)
	registry.update_global_away_cache(True)
	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path), session_registry=sessions)
	before = len(registry.conversations)
	await handlers.notify_human("hello again", "Claude", cli_session_id=sid, cwd="C:/X")
	assert len(registry.conversations) == before
	_assert_revived(registry, conv_id, sid)
	assert any(m["channel_id"] == conv_id and m["content"] == "hello again" for m in backend.channel_messages)


@pytest.mark.asyncio
async def test_opening_an_approval_revives():
	registry, backend, conv_id, sid, sessions = await _dormant_world()
	logger = MagicMock()
	logger.info = AsyncMock()
	logger.surface_error = AsyncMock()

	async def _resolve(_sid, _cwd):
		return conv_id, "Claude"

	approvals = ApprovalRegistry(registry, sessions, backend, logger, resolve_conversation=_resolve)
	await approvals.open(cli_session_id=sid, agent_id=None, tool="Bash", tool_input={"command": "ls"}, cwd="C:/X")
	_assert_revived(registry, conv_id, sid)


@pytest.mark.asyncio
async def test_the_round_trip_delivers_the_message_sent_while_dormant():
	from server.inbound import deliver_human_message
	from server.main import _build_inbox_route
	from tests.test_gateway_notify_human import RecordingBackend
	registry, _fake, conv_id, sid, sessions, clock = _bound_live_member()
	backend = RecordingBackend()
	sessions.get(sid).state = "lost"
	clock["t"] = 1101.0
	log = _Log()
	assert await sweep_lost_members(
		registry, sessions, backend, log, poll_lost_after_seconds=90, lost_after_seconds=900, now=_fixed_now,
	) == [sid]
	await _pump()
	await deliver_human_message(registry, backend, sessions, log, conv_id, "are you there")
	app = Starlette()
	app.add_route("/sessions/{sid}/inbox", _build_inbox_route(registry, sessions, backend, log), methods=["GET"])
	body = TestClient(app).get(f"/sessions/{sid}/inbox").json()
	assert len(body["notices"]) == 1 and body["notices"][0].endswith("are you there")
	_assert_revived(registry, conv_id, sid)
	assert list(registry.conversations) == [conv_id]


@pytest.mark.asyncio
async def test_a_poll_after_session_end_does_not_revive():
	from server.main import _build_inbox_route
	registry, backend, conv_id, sid, sessions = await _dormant_world()
	sessions.get(sid).state = "lost"
	sessions.record_session_end(sid, reason="logout", ended_at=_fixed_now())
	app = Starlette()
	app.add_route("/sessions/{sid}/inbox", _build_inbox_route(registry, sessions, backend, _Log()), methods=["GET"])
	assert TestClient(app).get(f"/sessions/{sid}/inbox").status_code == 200
	member = registry.conversations[conv_id].members_active[sid]
	assert member.alive is False
	assert sid not in registry.session_to_conversation_id
