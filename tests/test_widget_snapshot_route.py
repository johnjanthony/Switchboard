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
									  "name": None, "name_source": None, "title_state": None,
									  "effort": None}}
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
async def test_an_unknown_ring_no_longer_creates_a_session():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	session_registry = SessionRegistry()
	route = _build_widget_snapshot_route(store, backend, _FakeLogger(), session_registry)
	body = {"rings": [{"session_id": "unseen-session", "pct": 0.3, "model": "sonnet"}], "quota": None, "pushed_at": "t"}
	resp = await route(_request(body))
	assert resp.status_code == 200
	assert session_registry.get("unseen-session") is None
	assert "unseen-session" in backend.rings  # widget/rings still carries it for consumers not yet moved


@pytest.mark.asyncio
async def test_only_the_tab_title_marker_flows_into_a_known_session():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	session_registry = SessionRegistry()
	session_registry.record_session_start("sid-1", cwd="C:/Work/X")
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
	assert (rec.name, rec.model, rec.title_state) == (None, None, "star")


@pytest.mark.asyncio
async def test_ring_effort_no_longer_reaches_the_session_record():
	store, backend = WidgetSnapshotStore(), _FakeBackend()
	session_registry = SessionRegistry()
	session_registry.record_session_start("sid-1", cwd="C:/Work/X")
	route = _build_widget_snapshot_route(store, backend, _FakeLogger(), session_registry)
	body = {
		"rings": [{"session_id": "sid-1", "pct": 0.3, "model": "claude-opus-5-5", "effort": "xhigh"}],
		"quota": None,
		"pushed_at": "2026-06-25T00:00:00+00:00",
	}
	resp = await route(_request(body))
	assert resp.status_code == 200
	assert session_registry.get("sid-1").effort is None
	assert backend.rings["sid-1"]["effort"] == "xhigh"
