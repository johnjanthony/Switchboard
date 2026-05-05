import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
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
	app.add_route("/agent_status", _build_agent_status_route(handlers), methods=["POST"])
	return app


def test_post_agent_status_returns_200_and_calls_backend(cfg, logger, tmp_path):
	from server.canonicalization import canonicalize_cwd
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	# Handler is gated on away-mode — enable it for the test cwd
	registry.update_cwd_override_cache(canonicalize_cwd(str(tmp_path)), True)
	app = _build_app(handlers)

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"cwd": str(tmp_path),
			"state": "tool:Bash",
			"detail": "npm test",
		})
		assert resp.status_code == 200

	assert len(backend.agent_status_writes) == 1
	_, sender, state, detail = backend.agent_status_writes[0]
	assert state == "tool:Bash"
	assert detail == "npm test"
	assert sender == "Claude"  # cold-start fallback


def test_post_agent_status_returns_200_when_at_desk_no_backend_call(cfg, logger, tmp_path):
	"""HTTP layer always returns 200, but the handler gate skips the write
	when the cwd is not in away-mode."""
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	app = _build_app(handlers)
	# Away mode NOT enabled — registry default is at-desk

	with TestClient(app) as client:
		resp = client.post("/agent_status", json={
			"cwd": str(tmp_path),
			"state": "tool:Bash",
			"detail": "npm test",
		})
		assert resp.status_code == 200

	# Hook contract: 200 even though nothing was written
	assert backend.agent_status_writes == []


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
		resp = client.post("/agent_status", json={"cwd": str(tmp_path)})
		assert resp.status_code == 200
	assert backend.agent_status_writes == []
