"""Tests for the session-registry ingest routes: /session_start, /agent_status
upsert ordering, and GET /sessions."""

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.session_registry import SessionRegistry
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


def _build_app(handlers, session_registry, registry=None, logger=None):
	from server.main import (
		_build_agent_status_route,
		_build_away_mode_route,
		_build_session_start_route,
		_build_sessions_route,
	)
	app = Starlette()
	app.add_route("/session_start", _build_session_start_route(session_registry, logger), methods=["POST"])
	app.add_route("/sessions", _build_sessions_route(session_registry), methods=["GET"])
	app.add_route("/agent_status", _build_agent_status_route(handlers, session_registry), methods=["POST"])
	if registry is not None:
		app.add_route("/away-mode", _build_away_mode_route(registry, session_registry), methods=["GET"])
	return app


def test_session_start_upserts_idle_record_with_cwd(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/session_start", json={
			"session_id": "s1",
			"cwd": "C:/Work/X",
			"source": "startup",
		})
		assert resp.status_code == 200

	rec = session_registry.get("s1")
	assert rec is not None
	assert rec.state == "idle"
	assert rec.cwd == "C:/Work/X"


def test_session_start_missing_session_id_leaves_registry_unchanged(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/session_start", json={"cwd": "C:/Work/X"})
		assert resp.status_code == 200

	assert session_registry.snapshot() == []


def test_agent_status_discovers_unknown_session_even_when_away_mode_off(cfg, logger):
	"""The registry upsert happens before the away-mode gate: an unknown session
	is discovered in the roster even though the conversation-status write itself
	is dropped (away mode off)."""
	registry = Registry()
	registry.global_away_mode = False
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s2",
			"state": "tool:Bash",
			"detail": "x",
			"event": "PreToolUse",
		})
		assert resp.status_code == 200

	rec = session_registry.get("s2")
	assert rec is not None
	assert rec.state == "active"
	# Away mode is off, so the conversation-status write must still be dropped.
	assert backend.agent_status_writes == []


def test_agent_status_clear_maps_to_awaiting_human(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s2",
			"state": "clear",
			"event": "PreToolUse",
		})
		assert resp.status_code == 200

	rec = session_registry.get("s2")
	assert rec is not None
	assert rec.state == "awaiting_human"


def test_agent_status_without_event_field_bumps_last_event_at_only(cfg, logger):
	"""An old-plugin hook body with no 'event' field still discovers/touches the
	session (last_event_at bumped) but does not change state via mapping."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	session_registry.record_session_start("s3", cwd="C:/Work/Y")
	before = session_registry.get("s3").last_event_at
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s3",
			"state": "thinking",
		})
		assert resp.status_code == 200

	rec = session_registry.get("s3")
	assert rec is not None
	assert rec.state == "idle"
	assert rec.last_event_at >= before


def test_get_sessions_returns_recorded_payloads(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		client.post("/session_start", json={"session_id": "s1", "cwd": "C:/Work/X", "source": "startup"})
		client.post("/agent_status", json={
			"session_id": "s2", "state": "tool:Bash", "detail": "x", "event": "PreToolUse",
		})
		resp = client.get("/sessions")
		assert resp.status_code == 200
		body = resp.json()

	ids = {s["cli_session_id"] for s in body["sessions"]}
	assert ids == {"s1", "s2"}


def test_away_mode_delivers_and_pops_queued_notice_for_session(cfg, logger):
	registry = Registry()
	registry.global_away_mode = True
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	session_registry.record_session_start("s1", cwd="C:/Work/X")
	session_registry.queue_notice("s1", "wake up")
	app = _build_app(handlers, session_registry, registry=registry)

	with TestClient(app) as client:
		resp = client.get("/away-mode", params={"session_id": "s1"})
		assert resp.status_code == 200
		assert resp.json() == {"active": True, "notices": ["wake up"]}

		resp2 = client.get("/away-mode", params={"session_id": "s1"})
		assert resp2.status_code == 200
		assert resp2.json() == {"active": True, "notices": []}


def test_away_mode_without_session_id_does_not_pop_notice(cfg, logger):
	registry = Registry()
	registry.global_away_mode = False
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	session_registry.record_session_start("s1", cwd="C:/Work/X")
	session_registry.queue_notice("s1", "wake up")
	app = _build_app(handlers, session_registry, registry=registry)

	with TestClient(app) as client:
		resp = client.get("/away-mode")
		assert resp.status_code == 200
		assert resp.json() == {"active": False, "notices": []}

	# The notice was never popped since no session_id was supplied.
	assert session_registry.pop_notices("s1") == ["wake up"]


def test_agent_status_user_prompt_submit_delivers_and_empties_queue(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	session_registry.record_session_start("s1", cwd="C:/Work/X")
	session_registry.queue_notice("s1", "wake up")
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "thinking",
			"event": "UserPromptSubmit",
		})
		assert resp.status_code == 200
		assert resp.json() == {"notices": ["wake up"]}

	assert session_registry.pop_notices("s1") == []


def test_agent_status_pre_tool_use_does_not_pop_notice(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	session_registry.record_session_start("s1", cwd="C:/Work/X")
	session_registry.queue_notice("s1", "wake up")
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "tool:Bash",
			"event": "PreToolUse",
		})
		assert resp.status_code == 200
		assert resp.json() == {"notices": []}

	# The queue is untouched: PreToolUse has no delivery channel for it.
	assert session_registry.pop_notices("s1") == ["wake up"]


def test_agent_status_computes_in_tool_from_event_pair(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "tool:Bash",
			"event": "PreToolUse",
		})
		assert resp.status_code == 200
		assert session_registry.get("s1").in_tool is True

		resp2 = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "thinking",
			"event": "PostToolUse",
		})
		assert resp2.status_code == 200
		assert session_registry.get("s1").in_tool is False

		# The await special maps to awaiting_agent, not a real tool.
		resp3 = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "waiting",
			"event": "PreToolUse",
		})
		assert resp3.status_code == 200
		assert session_registry.get("s1").in_tool is False


def test_agent_status_cwd_fills_empty_record_but_does_not_overwrite(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	app = _build_app(handlers, session_registry)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "thinking",
			"event": "PreToolUse",
			"cwd": "C:/Work/X",
		})
		assert resp.status_code == 200

		rec = session_registry.get("s1")
		assert rec is not None
		assert rec.cwd == "C:/Work/X"

		resp2 = client.post("/agent_status", json={
			"session_id": "s1",
			"state": "thinking",
			"event": "PreToolUse",
			"cwd": "C:/Work/Y",
		})
		assert resp2.status_code == 200

	rec2 = session_registry.get("s1")
	assert rec2 is not None
	assert rec2.cwd == "C:/Work/X"


class _FakeSurfaceErrorLogger:
	def __init__(self):
		self.details = []

	async def surface_error(self, detail, correlation=None):
		self.details.append(detail)


def test_session_start_resume_with_changed_id_surfaces_sentinel(cfg, logger):
	"""If a spawn-driven resume comes back under a different session id than the
	one it was resumed from, /session_start must log the tripwire but still
	record the new session (source == resume never blocks ingest)."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	session_registry = SessionRegistry()
	session_registry.note_spawn_resume("sess-OLD", "C:/Work/X")
	fake_logger = _FakeSurfaceErrorLogger()
	app = _build_app(handlers, session_registry, logger=fake_logger)

	with TestClient(app) as client:
		resp = client.post("/session_start", json={
			"session_id": "sess-NEW",
			"cwd": "C:/Work/X",
			"source": "resume",
		})
		assert resp.status_code == 200

	assert any("resume_id_change_detected" in d for d in fake_logger.details)
	assert session_registry.get("sess-NEW") is not None
