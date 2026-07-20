"""REV-109: the unauthenticated POST routes (/widget-snapshot, /session_start,
/agent_status) get a coarse per-route token bucket. Hooks fail open on
non-200, so 429 is safe for every legitimate caller."""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from server.logging_jsonl import JsonlLogger
from server.rate_limiter import RateLimiter


def _app(limiter, log_path):
	from server.main import _with_route_limit

	logger = JsonlLogger(str(log_path))

	async def ok(request):
		return JSONResponse({"ok": True})

	app = Starlette()
	app.add_route("/a", _with_route_limit(ok, limiter, "/a", logger), methods=["POST"])
	app.add_route("/b", _with_route_limit(ok, limiter, "/b", logger), methods=["POST"])
	return app


def test_route_limit_returns_429_when_exhausted(tmp_path):
	with TestClient(_app(RateLimiter(2), tmp_path / "log.jsonl")) as c:
		assert c.post("/a").status_code == 200
		assert c.post("/a").status_code == 200
		r = c.post("/a")
		assert r.status_code == 429
		assert r.json() == {"error": "rate limited"}


def test_routes_have_independent_buckets(tmp_path):
	with TestClient(_app(RateLimiter(1), tmp_path / "log.jsonl")) as c:
		assert c.post("/a").status_code == 200
		assert c.post("/a").status_code == 429
		assert c.post("/b").status_code == 200


def test_route_limit_disabled_at_zero(tmp_path):
	with TestClient(_app(RateLimiter(0), tmp_path / "log.jsonl")) as c:
		for _ in range(5):
			assert c.post("/a").status_code == 200


def test_429_writes_jsonl_audit(tmp_path):
	log_path = tmp_path / "log.jsonl"
	with TestClient(_app(RateLimiter(1), log_path)) as c:
		c.post("/a")
		c.post("/a")
	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	limited = [e for e in events if e["event"] == "rate_limited"]
	# JsonlLogger stamps a "ts" field on every event, so check the fields
	# _with_route_limit controls rather than an exact-dict match.
	assert len(limited) == 1
	assert limited[0]["conversation_id"] == "/a"
	assert limited[0]["tool"] == "http_route"
