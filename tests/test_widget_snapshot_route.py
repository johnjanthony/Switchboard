"""Tests for POST /widget-snapshot ingest + on-change fan-out."""

import json

import pytest

from server.main import _build_widget_snapshot_route
from server.session_registry import SessionRegistry
from server.widget_snapshot import WidgetSnapshotStore


def _request(body: dict | None, raw: bytes | None = None):
	from starlette.requests import Request

	payload = raw if raw is not None else json.dumps(body).encode()

	async def receive():
		return {"type": "http.request", "body": payload, "more_body": False}

	scope = {"type": "http", "method": "POST", "headers": [], "query_string": b""}
	return Request(scope, receive)


class _FakeBackend:
	def __init__(self):
		self.rings = None
		self.quota = "unset"
		self.pushed = None
		self.ring_writes = 0
		self.quota_writes = 0

	async def write_widget_rings(self, rings):
		self.rings = rings
		self.ring_writes += 1

	async def write_widget_quota(self, quota):
		self.quota = quota
		self.quota_writes += 1

	async def write_widget_pushed_at(self, ts):
		self.pushed = ts


class _FakeLogger:
	async def surface_error(self, msg):
		pass


@pytest.mark.asyncio
async def test_first_push_writes_rings_keyed_by_session_id():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger())
	body = {
		"rings": [{"session_id": "abc", "pct": 0.4, "model": "opus", "status": "live",
				   "context_tokens": 80000, "window": 200000, "is_error": False}],
		"quota": {"session": {"pct": 0.5, "resets_at": "t"}},
		"pushed_at": "2026-06-25T00:00:00+00:00",
	}
	resp = await route(_request(body))
	out = json.loads(resp.body)
	assert resp.status_code == 200
	assert out["rings_changed"] is True and out["quota_changed"] is True
	assert backend.rings == {"abc": {"pct": 0.4, "model": "opus", "status": "live",
									  "context_tokens": 80000, "window": 200000, "is_error": False,
									  "name": None, "name_source": None, "title_state": None}}
	assert backend.pushed == "2026-06-25T00:00:00+00:00"


@pytest.mark.asyncio
async def test_unchanged_repush_skips_writes():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger())
	body = {"rings": [{"session_id": "abc", "pct": 0.4}], "quota": None, "pushed_at": "t0"}
	await route(_request(body))
	backend.ring_writes = 0
	await route(_request({"rings": [{"session_id": "abc", "pct": 0.4}], "quota": None, "pushed_at": "t1"}))
	assert backend.ring_writes == 0
	# pushed_at is always written so staleness stays live.
	assert backend.pushed == "t1"


@pytest.mark.asyncio
async def test_rings_without_session_id_are_dropped():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger())
	body = {"rings": [{"pct": 0.4}, {"session_id": "ok", "pct": 0.2}], "quota": None, "pushed_at": "t"}
	await route(_request(body))
	assert set(backend.rings.keys()) == {"ok"}


@pytest.mark.asyncio
async def test_malformed_json_returns_400():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger())
	resp = await route(_request(None, raw=b"not json"))
	assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rings_not_a_list_returns_400():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger())
	resp = await route(_request({"rings": {"bad": 1}, "quota": None, "pushed_at": "t"}))
	assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_ring_discovers_session_in_registry():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	session_registry = SessionRegistry()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger(), session_registry)
	body = {
		"rings": [{"session_id": "unseen-session", "pct": 0.3, "model": "sonnet"}],
		"quota": None,
		"pushed_at": "2026-06-25T00:00:00+00:00",
	}
	resp = await route(_request(body))
	assert resp.status_code == 200
	rec = session_registry.get("unseen-session")
	assert rec is not None
	assert rec.source == "rings"


@pytest.mark.asyncio
async def test_ring_name_and_source_flow_into_session_registry():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	session_registry = SessionRegistry()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger(), session_registry)
	body = {
		"rings": [{"session_id": "sid-1", "pct": 0.3, "model": "sonnet",
				   "name": "Fixing tests", "name_source": "ai-title", "title_state": "star"}],
		"quota": None,
		"pushed_at": "2026-06-25T00:00:00+00:00",
	}
	resp = await route(_request(body))
	assert resp.status_code == 200
	rec = session_registry.get("sid-1")
	assert rec is not None
	assert rec.name == "Fixing tests"
	assert rec.title_state == "star"


@pytest.mark.asyncio
async def test_ring_name_retitles_its_single_agent_conversation():
	"""The route is the only place ring names arrive, so it is where the
	session-title sync has to be wired."""
	from server.registry import Registry
	from tests.conftest import make_active_conversation

	store, backend = WidgetSnapshotStore(), _FakeBackend()
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-w1", member_session_id="sid-1")
	conv.title_source = "default"
	registry.conversations["conv-w1"] = conv
	registry.bind_session("sid-1", "conv-w1")
	route = _build_widget_snapshot_route(store, backend, _FakeLogger(), SessionRegistry(), registry)

	resp = await route(_request({
		"rings": [{"session_id": "sid-1", "name": "Retitled by Watchtower", "name_source": "ai-title"}],
		"quota": None,
		"pushed_at": "2026-06-25T00:00:00+00:00",
	}))

	assert resp.status_code == 200
	assert conv.title == "Retitled by Watchtower"
	assert conv.title_source == "session"


@pytest.mark.asyncio
async def test_title_sync_failure_does_not_fail_the_snapshot_push():
	"""Watchtower fails open on non-200, but a rejected push also hides ring and
	quota data that wrote fine. A title-write failure must stay isolated."""
	from server.registry import Registry
	from tests.conftest import make_active_conversation

	class _ExplodingTitleBackend(_FakeBackend):
		async def write_conversation_title(self, conv_id, title, title_source=None):
			raise RuntimeError("firebase down")

	store, backend = WidgetSnapshotStore(), _ExplodingTitleBackend()
	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-w2", member_session_id="sid-2")
	conv.title_source = "default"
	registry.conversations["conv-w2"] = conv
	registry.bind_session("sid-2", "conv-w2")
	route = _build_widget_snapshot_route(store, backend, _FakeLogger(), SessionRegistry(), registry)

	resp = await route(_request({
		"rings": [{"session_id": "sid-2", "pct": 0.3, "name": "Doomed title"}],
		"quota": None,
		"pushed_at": "2026-06-25T00:00:00+00:00",
	}))

	assert resp.status_code == 200
	assert backend.rings is not None, "ring write must still land when the title write fails"
