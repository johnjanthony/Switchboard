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
	dispatch_away_mode_commands,
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


def _build_away_mode_route(registry: Registry):
	from server.canonicalization import canonicalize_cwd, CanonicalizationError
	async def away_mode(request: Request):
		cwd_raw = request.query_params.get("cwd", "")
		if not cwd_raw:
			return JSONResponse({"active": False})
		try:
			canonical = canonicalize_cwd(cwd_raw)
		except CanonicalizationError:
			return JSONResponse({"active": False})
		return JSONResponse({"active": registry.is_away_mode_active(canonical)})
	return away_mode


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
		cwd: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> str:
		"""Block until John responds from his phone. Returns the response text,
		or '__TIMEOUT__' if the timeout window elapses.

		cwd: Your current working directory (from $PWD or the system prompt's
		'Primary working directory' field). Used as the channel routing key —
		canonicalized server-side.

		title: Optional. Session label shown on John's phone tab. SKILL mandates
		first-call set; omit on later calls when scope hasn't changed. Server
		truncates to 80 chars."""
		return await handlers.ask_human(question, cwd, sender, title, format, suggestions)

	@mcp.tool()
	async def notify_human(
		message: str,
		cwd: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
	) -> str:
		"""Fire a status message to John. Non-blocking. cwd routes to the channel."""
		return await handlers.notify_human(message, cwd, sender, title, format)

	@mcp.tool()
	async def send_document_human(
		path: str,
		cwd: str,
		sender: str,
		title: str | None = None,
		caption: str | None = None,
	) -> str:
		"""Deliver a file to John. Non-blocking. path is relative to cwd. Max 5 MB."""
		return await handlers.send_document_human(path, cwd, sender, title, caption)

	@mcp.tool()
	async def message_and_await_agent(
		cwd: str,
		sender: str,
		title: str | None = None,
		message: str | None = None,
	) -> str:
		"""Send to your collab partner and block until they reply.
		cwd is the shared session key. sender is your unique display name.
		Omit message on your first call if you are Agent 2."""
		return await handlers.message_and_await_agent(cwd, sender, title, message)

	@mcp.tool()
	async def end_collab(
		cwd: str,
		sender: str,
		message: str | None = None,
		hand_off_to_human: bool = True,
	) -> str:
		"""End the collab session for this cwd. Non-blocking. Resolves any
		partner's pending message_and_await_agent with sentinel
		'__COLLAB_ENDED__\\n<message>' then purges the session so future calls
		create a fresh session.

		hand_off_to_human=True (default): caller is the designated reporter.
		hand_off_to_human=False: partner is the reporter; caller exits silently.
		See skill/SKILL.md 'Ending a collab session' for full protocol."""
		return await handlers.end_collab(cwd, sender, message, hand_off_to_human)

	@mcp.tool()
	async def enter_away_mode(cwd: str) -> str:
		"""Mark this Switchboard session (cwd) as 'John is away'. Sets the
		per-cwd override True. Idempotent."""
		return await handlers.enter_away_mode(cwd)

	@mcp.tool()
	async def exit_away_mode(cwd: str) -> str:
		"""Mark this Switchboard session (cwd) as 'John is back'. Sets the
		per-cwd override False. Idempotent."""
		return await handlers.exit_away_mode(cwd)

	return mcp


async def _wire_away_mode_mirror(registry: "Registry", backend: "MessengerBackend") -> None:
	"""Register a post-set callback that mirrors the away-mode flag to Firebase,
	and perform a startup push so Firebase reflects the sidecar truth."""
	loop = asyncio.get_running_loop()

	def _on_change(cwd: "str | None", active: bool) -> None:
		if cwd is None:
			effective = registry.global_away() or bool(registry.cwd_overrides())
			loop.create_task(backend.write_away_mode_mirror(None, effective))
		else:
			loop.create_task(backend.write_away_mode_mirror(cwd, active))

	registry.set_away_mode_callback(_on_change)
	initial = registry.global_away() or bool(registry.cwd_overrides())
	await backend.write_away_mode_mirror(None, initial)


async def _run(config: Config) -> None:
	logger = JsonlLogger(config.log_path)
	away_mode_path = _Path(config.log_path).parent / "away-mode.json"
	registry = Registry(away_mode_path=away_mode_path)

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

	await _wire_away_mode_mirror(registry, backend)

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
	app.add_route("/away-mode", _build_away_mode_route(registry), methods=["GET"])

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

	away_cmd_task = asyncio.create_task(
		dispatch_away_mode_commands(registry, backend, handlers, logger)
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
		away_cmd_task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await dispatch_task
		with contextlib.suppress(asyncio.CancelledError):
			await spawn_task
		with contextlib.suppress(asyncio.CancelledError):
			await inject_task
		with contextlib.suppress(asyncio.CancelledError):
			await away_cmd_task
		await backend.aclose()


def run() -> None:
	config = load_config()
	try:
		asyncio.run(_run(config))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	run()
