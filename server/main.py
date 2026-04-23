"""Switchboard entry point — wires dependencies and runs the server."""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
import signal
from pathlib import Path as _Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.requests import Request

from server.config import Config, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_commands,
	dispatch_inject_queue,
	dispatch_responses,
)
from server.logging_jsonl import JsonlLogger
from server.messenger import MultiBackend
from server.registry import Registry
from server.spawn import SpawnHandler
from server.rate_limiter import RateLimiter
from server.android import AndroidBackend
from server.firebase import FirebaseBackend


async def _notify_lost_collab_sessions(sidecar_path: _Path, backend) -> None:
	if not sidecar_path.exists():
		return
	try:
		entries = _json.loads(sidecar_path.read_text(encoding="utf-8"))
	except Exception:
		sidecar_path.unlink(missing_ok=True)
		return
	try:
		for entry in entries:
			channel_id = entry.get("channel_id", "unknown")
			try:
				await backend.write_channel_message(
					channel_id, "system", "notify",
					f"Switchboard restarted. Collab session `{channel_id}` was lost — agents will time out.",
					format="markdown",
				)
			except Exception:
				pass
	finally:
		sidecar_path.unlink(missing_ok=True)


def _build_fastmcp(handlers) -> FastMCP:
	mcp = FastMCP("switchboard", stateless_http=True)

	@mcp.tool()
	async def ask_human(
		question: str,
		channel_id: str,
		sender: str = "Claude",
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> str:
		"""Block until the developer responds from their phone. Returns the response
		text, or '__TIMEOUT__' if the timeout window elapses. Set format='markdown'
		for rich formatting. Pass suggestions=['yes','no'] for tap-able inline buttons."""
		return await handlers.ask_human(question, channel_id, sender, format, suggestions)

	@mcp.tool()
	async def notify_human(message: str, channel_id: str, sender: str = "Claude", format: str = "plain") -> str:
		"""Fire a status message to the developer. Non-blocking.
		Set format='markdown' for rich formatting."""
		return await handlers.notify_human(message, channel_id, sender, format)

	@mcp.tool()
	async def send_document_human(
		path: str, channel_id: str, sender: str = "Claude", caption: str | None = None
	) -> str:
		"""Deliver a file to the developer. Non-blocking.
		path must be relative to the project working directory. Max 5 MB."""
		return await handlers.send_document_human(path, channel_id, sender, caption)

	@mcp.tool()
	async def message_and_await_agent(
		channel_id: str, sender: str, message: str | None = None
	) -> str:
		"""Send a message to your collaboration partner and block until they reply.
		channel_id and sender are provided in your spawn prompt.
		Omit message on your first call if you are Agent 2."""
		return await handlers.message_and_await_agent(channel_id, sender, message)

	return mcp


async def _run(config: Config) -> None:
	logger = JsonlLogger(config.log_path)
	registry = Registry()

	backends = []

	if config.enable_android:
		android_backend = AndroidBackend(logger=logger)
		backends.append(android_backend)

	if config.firebase_service_account_json and config.firebase_database_url:
		firebase_backend = FirebaseBackend(
			service_account_json=config.firebase_service_account_json,
			database_url=config.firebase_database_url,
			storage_bucket=config.firebase_storage_bucket,
			logger=logger
		)
		backends.append(firebase_backend)

	if len(backends) == 1:
		backend = backends[0]
	else:
		backend = MultiBackend(backends)

	sidecar_path = _Path(config.log_path).parent / "collab-sessions.json"
	await _notify_lost_collab_sessions(sidecar_path, backend)

	limiter = RateLimiter(config.rate_limit)
	handlers = build_tool_handlers(config, registry, backend, logger, limiter)
	mcp = _build_fastmcp(handlers)

	app = mcp.streamable_http_app()

	async def healthz(request: Request):
		return JSONResponse({
			"pending_count": registry.pending_count,
			"oldest_pending_age_seconds": registry.oldest_pending_age_seconds,
			"total_answered": registry.total_answered,
		})

	app.add_route("/healthz", healthz, methods=["GET"])

	if config.enable_android:
		async def get_questions(request: Request):
			return JSONResponse(android_backend.get_pending_questions())

		async def post_reply(request: Request):
			data = await request.json()
			request_id = data.get("request_id")
			text = data.get("text")
			if not request_id or text is None:
				return JSONResponse({"error": "missing fields"}, status_code=400)

			correlation = f"android_{request_id}"
			await android_backend.simulate_response(correlation, text)
			return JSONResponse({"status": "ok"})

		app.add_route("/android/questions", get_questions, methods=["GET"])
		app.add_route("/android/reply", post_reply, methods=["POST"])

	uv_config = uvicorn.Config(
		app,
		host=config.host,
		port=config.port,
		log_level="info",
	)
	server = uvicorn.Server(uv_config)

	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)

	spawn_handler = SpawnHandler(config, backend, logger, registry)
	spawn_task = asyncio.create_task(
		dispatch_commands(spawn_handler, backend, logger)
	)

	inject_task = asyncio.create_task(
		dispatch_inject_queue(registry, backend, logger)
	)

	loop = asyncio.get_running_loop()

	def _request_stop() -> None:
		server.should_exit = True

	# Windows does not support add_signal_handler reliably; swallow the
	# NotImplementedError and fall back to KeyboardInterrupt in run().
	for sig_name in ("SIGINT", "SIGTERM"):
		sig = getattr(signal, sig_name, None)
		if sig is None:
			continue
		with contextlib.suppress(NotImplementedError):
			loop.add_signal_handler(sig, _request_stop)

	try:
		await server.serve()
	finally:
		dispatch_task.cancel()
		spawn_task.cancel()
		inject_task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await dispatch_task
		with contextlib.suppress(asyncio.CancelledError):
			await spawn_task
		with contextlib.suppress(asyncio.CancelledError):
			await inject_task
		await backend.aclose()


def run() -> None:
	config = load_config()
	try:
		asyncio.run(_run(config))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	run()
