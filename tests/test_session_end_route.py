"""POST /session_end: the Claude Code mod's session.end hook reports the end
directly instead of writing a marker file."""

from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.registry import Registry
from server.session_registry import SessionRegistry


def _app(registry, sessions):
	from server.main import _build_session_end_route
	app = Starlette()
	app.add_route("/session_end", _build_session_end_route(registry, sessions, None, None), methods=["POST"])
	return app


def test_session_end_marks_the_record_ended():
	sessions = SessionRegistry()
	sessions.record_session_start("s1", cwd="C:/Work/X")
	with TestClient(_app(Registry(), sessions)) as client:
		resp = client.post("/session_end", json={"session_id": "s1", "reason": "prompt_input_exit"})
	assert resp.status_code == 200
	assert sessions.get("s1").state == "ended"


def test_session_end_unbinds_the_session_from_its_conversation():
	registry = Registry()
	registry.bind_session("s1", "conv-1")
	sessions = SessionRegistry()
	sessions.record_session_start("s1", cwd="C:/Work/X")
	with TestClient(_app(registry, sessions)) as client:
		client.post("/session_end", json={"session_id": "s1", "reason": "other"})
	assert registry.session_to_conversation_id.get("s1") is None


def test_session_end_without_a_session_id_changes_nothing():
	sessions = SessionRegistry()
	sessions.record_session_start("s1", cwd="C:/Work/X")
	with TestClient(_app(Registry(), sessions)) as client:
		resp = client.post("/session_end", json={"reason": "other"})
	assert resp.status_code == 200
	assert sessions.get("s1").state != "ended"


def test_session_end_with_a_malformed_body_is_still_200():
	with TestClient(_app(Registry(), SessionRegistry())) as client:
		assert client.post("/session_end", content=b"not json").status_code == 200
