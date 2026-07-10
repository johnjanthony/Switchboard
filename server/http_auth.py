"""Shared-secret gate for the HTTP/MCP surface (REV-003, T-141 option (a)).

One pure-ASGI wrapper around the whole composed app (MCP transport, custom
routes, dashboard mount). Loopback peers are exempt: the threat model is
network exposure under a non-loopback bind (SWITCHBOARD_HOST=0.0.0.0 for
WSL), not local processes - Windows-side hooks, Watchtower, the Operator
browser, and the local CLI all arrive via 127.0.0.1 and keep working without
a token. Every other peer must present `Authorization: Bearer <token>` on
every path except /healthz. Mechanism record:
docs/2026-06-11-control-surface-hardening.md.
"""

from __future__ import annotations

import hmac
import json

_EXEMPT_PATHS = frozenset({"/healthz"})
_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1"})


class TokenAuthMiddleware:
	"""With token=None this is a pass-through (loopback-only deployments);
	load_config guarantees a token exists whenever the bind host is
	non-loopback, so the open configuration cannot reach uvicorn."""

	def __init__(self, app, token: str | None):
		self._app = app
		self._expected = f"Bearer {token}".encode() if token else None

	async def __call__(self, scope, receive, send):
		# Only HTTP is gated: lifespan must pass, and no websocket routes exist
		# (FastMCP streamable-http uses plain HTTP + SSE).
		if self._expected is None or scope["type"] != "http":
			await self._app(scope, receive, send)
			return
		if scope.get("path", "") in _EXEMPT_PATHS:
			await self._app(scope, receive, send)
			return
		client = scope.get("client")
		if client is not None and client[0] in _LOOPBACK_PEERS:
			await self._app(scope, receive, send)
			return
		provided = None
		for name, value in scope.get("headers", []):
			if name == b"authorization":
				provided = value
				break
		if provided is not None and hmac.compare_digest(provided, self._expected):
			await self._app(scope, receive, send)
			return
		body = json.dumps({"error": "unauthorized"}).encode()
		await send({
			"type": "http.response.start",
			"status": 401,
			"headers": [
				(b"content-type", b"application/json"),
				(b"content-length", str(len(body)).encode()),
			],
		})
		await send({"type": "http.response.body", "body": body})
