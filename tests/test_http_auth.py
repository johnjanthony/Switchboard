"""REV-003: shared-secret gate for the HTTP/MCP surface. Loopback peers and
/healthz are exempt; everything else needs `Authorization: Bearer <token>`
when a token is configured. With token=None the middleware is a pass-through."""

from __future__ import annotations

import json

import pytest

from server.http_auth import TokenAuthMiddleware


class _InnerApp:
	def __init__(self):
		self.calls: list[str] = []

	async def __call__(self, scope, receive, send):
		self.calls.append(scope.get("path", "<non-http>"))
		if scope["type"] == "http":
			await send({"type": "http.response.start", "status": 200, "headers": []})
			await send({"type": "http.response.body", "body": b"inner-ok"})


def _scope(path="/stats", client=("203.0.113.9", 4242), headers=None):
	return {
		"type": "http", "method": "GET", "path": path,
		"headers": headers if headers is not None else [],
		"client": client, "query_string": b"",
	}


async def _call(mw, scope):
	sent = []

	async def send(msg):
		sent.append(msg)

	async def receive():
		return {"type": "http.request", "body": b"", "more_body": False}

	await mw(scope, receive, send)
	return sent


def _status(sent):
	return sent[0]["status"]


@pytest.mark.asyncio
async def test_no_token_passes_everything_through():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token=None)
	sent = await _call(mw, _scope())
	assert inner.calls == ["/stats"]
	assert _status(sent) == 200


@pytest.mark.asyncio
async def test_non_loopback_without_header_gets_401():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	sent = await _call(mw, _scope())
	assert inner.calls == []
	assert _status(sent) == 401
	body = json.loads(sent[1]["body"])
	assert body == {"error": "unauthorized"}


@pytest.mark.asyncio
async def test_non_loopback_wrong_token_gets_401():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	sent = await _call(mw, _scope(headers=[(b"authorization", b"Bearer wrong")]))
	assert inner.calls == []
	assert _status(sent) == 401


@pytest.mark.asyncio
async def test_non_loopback_correct_token_passes():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	sent = await _call(mw, _scope(headers=[(b"authorization", b"Bearer sekrit")]))
	assert inner.calls == ["/stats"]
	assert _status(sent) == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
async def test_loopback_peer_exempt(peer):
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	sent = await _call(mw, _scope(client=(peer, 55555)))
	assert inner.calls == ["/stats"]
	assert _status(sent) == 200


@pytest.mark.asyncio
async def test_healthz_exempt_without_header():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	sent = await _call(mw, _scope(path="/healthz"))
	assert inner.calls == ["/healthz"]
	assert _status(sent) == 200


@pytest.mark.asyncio
async def test_missing_client_fails_closed():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	sent = await _call(mw, _scope(client=None))
	assert inner.calls == []
	assert _status(sent) == 401


@pytest.mark.asyncio
async def test_lifespan_scope_passes_through():
	inner = _InnerApp()
	mw = TokenAuthMiddleware(inner, token="sekrit")
	await _call(mw, {"type": "lifespan"})
	assert inner.calls == ["<non-http>"]


def test_integration_starlette_401_then_200():
	# TestClient's default peer is ("testclient", 50000) - non-loopback, so the
	# gate engages end-to-end through the Starlette stack.
	from starlette.applications import Starlette
	from starlette.responses import JSONResponse
	from starlette.testclient import TestClient

	async def ok(request):
		return JSONResponse({"ok": True})

	app = Starlette()
	app.add_route("/x", ok, methods=["GET"])
	with TestClient(TokenAuthMiddleware(app, token="sekrit")) as c:
		assert c.get("/x").status_code == 401
		assert c.get("/x", headers={"Authorization": "Bearer sekrit"}).status_code == 200


def test_integration_starlette_loopback_client_exempt():
	from starlette.applications import Starlette
	from starlette.responses import JSONResponse
	from starlette.testclient import TestClient

	async def ok(request):
		return JSONResponse({"ok": True})

	app = Starlette()
	app.add_route("/x", ok, methods=["GET"])
	with TestClient(TokenAuthMiddleware(app, token="sekrit"), client=("127.0.0.1", 50000)) as c:
		assert c.get("/x").status_code == 200
