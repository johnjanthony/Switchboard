"""Switchboard entry point — wires dependencies and runs the server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.requests import Request

from server.config import Config, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_commands,
	dispatch_responses,
)
from server.logging_jsonl import JsonlLogger
from server.messenger import MultiBackend
from server.registry import Registry
from server.spawn import SpawnHandler
from server.telegram import TelegramBackend
from server.android import AndroidBackend
from server.firebase import FirebaseBackend


def _build_fastmcp(handlers) -> FastMCP:
	mcp = FastMCP("switchboard")

	@mcp.tool()
	async def ask_human(
		question: str,
		agent_id: str,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> str:
		"""Block until the developer responds from their phone. Returns
		the response text, or the sentinel '__TIMEOUT__' if the timeout
		window elapses. Set format='markdown' to render the message with
		rich formatting (bold, italic, inline code, code blocks). Use
		standard Markdown syntax. Pass suggestions=['yes','no'] to render
		tap-able inline buttons; the tapped label is returned as the response."""
		return await handlers.ask_human(question, agent_id, format, suggestions)

	@mcp.tool()
	async def notify_human(message: str, agent_id: str, format: str = "plain") -> str:
		"""Fire a status message to the developer. Non-blocking.
		Set format='markdown' to render the message with rich formatting
		(bold, italic, inline code, code blocks). Use standard Markdown syntax."""
		return await handlers.notify_human(message, agent_id, format)

	@mcp.tool()
	async def send_document_human(
		path: str, agent_id: str, caption: str | None = None
	) -> str:
		"""Deliver a file to the developer on Telegram. Non-blocking.
		path must be relative to the project working directory (no absolute
		paths, no .. traversal). Max 5 MB. Sensitive filenames (.env, *.pem,
		*token*, *secret*, *.key, service-account.json) are rejected."""
		return await handlers.send_document_human(path, agent_id, caption)

	return mcp


async def _run(config: Config) -> None:
	# Silence httpx per-request INFO logs — they embed the bot token in the URL.
	logging.getLogger("httpx").setLevel(logging.WARNING)

	logger = JsonlLogger(config.log_path)
	registry = Registry()

	backends = []

	if config.enable_telegram and config.telegram_bot_token and config.telegram_chat_id:
		telegram_backend = TelegramBackend(
			token=config.telegram_bot_token,
			chat_id=config.telegram_chat_id,
			logger=logger,
		)
		backends.append(telegram_backend)

		# Preflight: verify token via getMe. Non-fatal per spec §7 — log and continue.
		try:
			await telegram_backend.preflight()
		except Exception as exc:
			logger.surface_error(f"telegram_preflight_failed: {exc}")

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

	handlers = build_tool_handlers(config, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	app = mcp.sse_app()

	if config.enable_android:
		@app.route("/android/questions", methods=["GET"])
		async def get_questions(request: Request):
			return JSONResponse(android_backend.get_pending_questions())

		@app.route("/android/reply", methods=["POST"])
		async def post_reply(request: Request):
			data = await request.json()
			request_id = data.get("request_id")
			text = data.get("text")
			if not request_id or text is None:
				return JSONResponse({"error": "missing fields"}, status_code=400)

			correlation = f"android_{request_id}"
			await android_backend.simulate_response(correlation, text)
			return JSONResponse({"status": "ok"})

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

	spawn_handler = SpawnHandler(config, backend, logger)
	spawn_task = asyncio.create_task(
		dispatch_commands(spawn_handler, backend, logger)
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
		with contextlib.suppress(asyncio.CancelledError):
			await dispatch_task
		with contextlib.suppress(asyncio.CancelledError):
			await spawn_task
		await backend.aclose()


def run() -> None:
	config = load_config()
	try:
		asyncio.run(_run(config))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	run()
