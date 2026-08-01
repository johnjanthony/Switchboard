import time

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
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


def _build_app(handlers):
	from server.main import _build_agent_status_route
	app = Starlette()
	app.add_route("/agent_status", _build_agent_status_route(handlers, SessionRegistry()), methods=["POST"])
	return app


def _build_app_with_sessions(handlers, sessions):
	from server.main import _build_agent_status_route
	app = Starlette()
	app.add_route("/agent_status", _build_agent_status_route(handlers, sessions), methods=["POST"])
	return app


def _make_active_registry(conv_id="conv-xyz", session_id="s-1", sender="Claude", cwd="C:/Work/X"):
	"""Return a Registry with one active conversation and a bound session."""
	registry = Registry()
	registry.global_away_mode = True
	conv = Conversation(id=conv_id, title="test")
	conv.created_at = time.time()
	conv.last_activity_at = conv.created_at
	m = ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=time.time(),
	)
	conv.members_active[session_id] = m
	registry.conversations[conv_id] = conv
	registry.bind_session(session_id, conv_id)
	return registry


def test_post_agent_status_returns_200_on_malformed_body(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", data="not json", headers={"Content-Type": "application/json"})
		assert resp.status_code == 200
	assert backend.agent_status_writes == []


def test_post_agent_status_returns_200_on_missing_fields(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		# Missing 'state'
		resp = client.post("/agent_status", json={"session_id": "s-1"})
		assert resp.status_code == 200
	assert backend.agent_status_writes == []


def test_handle_agent_status_writes_to_conversations_path(cfg, logger):
	"""A call with a valid session_id writes to /conversations/<conv_id>/agent_status/<sender>."""
	registry = _make_active_registry(conv_id="conv-xyz", session_id="s-1", sender="Claude")
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"state": "thinking",
			"detail": None,
			"session_id": "s-1",
		})
		assert resp.status_code == 200

	assert backend.agent_status_writes == [("conv-xyz", "Claude", "thinking", None)]


def test_handle_agent_status_clear_writes_conv_path(cfg, logger):
	"""state='clear' is passed through with the conv_id as the first argument."""
	registry = _make_active_registry(conv_id="conv-xyz", session_id="s-1", sender="Claude")
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"state": "clear",
			"detail": None,
			"session_id": "s-1",
		})
		assert resp.status_code == 200

	assert backend.agent_status_writes == [("conv-xyz", "Claude", "clear", None)]


def test_handle_agent_status_drops_write_when_no_session_id(cfg, logger):
	"""Without session_id the write is dropped — nowhere to route the status."""
	registry = _make_active_registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"state": "thinking",
			"detail": None,
			# no session_id
		})
		assert resp.status_code == 200

	assert backend.agent_status_writes == []


def test_handle_agent_status_drops_write_when_session_unbound(cfg, logger):
	"""An unbound session_id (not mapped to any conversation) drops the write."""
	registry = Registry()
	registry.global_away_mode = True
	# No conversation, no binding for "s-orphan"
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"state": "thinking",
			"detail": None,
			"session_id": "s-orphan",
		})
		assert resp.status_code == 200

	assert backend.agent_status_writes == []


def test_handle_agent_status_drops_write_when_away_mode_off(cfg, logger):
	"""Writes are dropped when away mode is off regardless of session binding."""
	registry = _make_active_registry()
	registry.global_away_mode = False
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"state": "thinking",
			"detail": None,
			"session_id": "s-1",
		})
		assert resp.status_code == 200

	assert backend.agent_status_writes == []


def test_post_agent_status_records_cli(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	sessions = SessionRegistry()
	app = _build_app_with_sessions(handlers, sessions)
	with TestClient(app) as client:
		client.post("/agent_status", json={
			"session_id": "agy-1", "state": "thinking", "event": "UserPromptSubmit",
			"cwd": "C:/Work/X", "cli": "antigravity",
		})
	assert sessions.get("agy-1").cli == "antigravity"


def test_post_agent_status_without_cli_keeps_default(cfg, logger):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	sessions = SessionRegistry()
	app = _build_app_with_sessions(handlers, sessions)
	with TestClient(app) as client:
		client.post("/agent_status", json={
			"session_id": "c-1", "state": "thinking", "event": "UserPromptSubmit", "cwd": "C:/Work/X",
		})
	assert sessions.get("c-1").cli == "claude"


def test_post_tool_use_pops_notices_and_is_at_most_once(cfg, logger):
	"""PostToolUse (Claude Code, no cli field) pops queued notices; a second
	identical POST returns empty because pop_notices clears on read."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	sessions = SessionRegistry()
	sessions.record_session_start("c-1", cwd="C:/Work/X")
	assert sessions.queue_notice("c-1", "hello") is True
	app = _build_app_with_sessions(handlers, sessions)
	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "c-1", "state": "thinking", "event": "PostToolUse", "cwd": "C:/Work/X",
		})
		assert resp.json() == {"notices": ["hello"]}
		resp2 = client.post("/agent_status", json={
			"session_id": "c-1", "state": "thinking", "event": "PostToolUse", "cwd": "C:/Work/X",
		})
		assert resp2.json() == {"notices": []}


def test_pre_tool_use_does_not_pop_notices(cfg, logger):
	"""PreToolUse must not pop - over-widening the predicate would silently eat
	notices before the agent ever gets a chance to see them."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	sessions = SessionRegistry()
	sessions.record_session_start("c-1", cwd="C:/Work/X")
	assert sessions.queue_notice("c-1", "hello") is True
	app = _build_app_with_sessions(handlers, sessions)
	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "c-1", "state": "tool:Bash", "event": "PreToolUse", "cwd": "C:/Work/X",
		})
		assert resp.json() == {"notices": []}
	assert sessions.pop_notices("c-1") == ["hello"]


def test_post_tool_use_from_antigravity_does_not_pop_notices(cfg, logger):
	"""The agy PostToolUse hook discards the response body, so popping there
	would destroy the notice with nothing to deliver it. Excluding cli ==
	'antigravity' leaves the notice for the next UserPromptSubmit POST."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	sessions = SessionRegistry()
	sessions.record_session_start("agy-1", cwd="C:/Work/X", cli="antigravity")
	assert sessions.queue_notice("agy-1", "hello") is True
	app = _build_app_with_sessions(handlers, sessions)
	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "agy-1", "state": "thinking", "event": "PostToolUse",
			"cwd": "C:/Work/X", "cli": "antigravity",
		})
		assert resp.json() == {"notices": []}
		resp2 = client.post("/agent_status", json={
			"session_id": "agy-1", "state": "thinking", "event": "UserPromptSubmit",
			"cwd": "C:/Work/X", "cli": "antigravity",
		})
		assert resp2.json() == {"notices": ["hello"]}


def test_user_prompt_submit_still_pops_notices(cfg, logger):
	"""UserPromptSubmit keeps its existing pop-on-read behavior, unchanged."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	sessions = SessionRegistry()
	sessions.record_session_start("c-1", cwd="C:/Work/X")
	assert sessions.queue_notice("c-1", "hello") is True
	app = _build_app_with_sessions(handlers, sessions)
	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"session_id": "c-1", "state": "thinking", "event": "UserPromptSubmit", "cwd": "C:/Work/X",
		})
		assert resp.json() == {"notices": ["hello"]}
