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

from server.config import Config, ConfigError, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_away_mode_commands,
	dispatch_commands,
	dispatch_inject_queue,
	dispatch_responses,
)
from server.gateway.bg_tasks import _spawn_bg
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.spawn import SpawnHandler
from server.rate_limiter import RateLimiter
from server.firebase import FirebaseBackend
from server.firebase_supervisor import LoopSupervisor
from server.messenger import AwayModeMirror


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


def _build_collab_partner_state_route(registry: Registry):
	"""Read-only route used by the turn-end hook (Option G / H9). Returns the
	state of the collab session at this cwd from the live agent's perspective:

	- `none` — no session for this cwd, or session has fewer than two enrolled agents
	- `live` — session exists, no agent is currently blocked in `_waiting`
	- `blocked` — at least one agent is blocked in `_waiting`. The hook treats
	  this as "your partner is blocked awaiting your reply" (in a 2-agent collab,
	  if anyone is blocked, the agent firing the Stop hook is by definition the
	  live one — they're generating output, not suspended on a tool await).

	Sender is intentionally NOT a query param: matching by sender is brittle
	when agents rename themselves (e.g. "Sparkles"), and the cwd-level signal
	is sufficient since collab is 2-agent and exactly one agent can be `live`
	at a time."""
	from server.canonicalization import canonicalize_cwd, CanonicalizationError
	async def collab_partner_state(request: Request):
		cwd_raw = request.query_params.get("cwd", "")
		if not cwd_raw:
			return JSONResponse({"state": "none"})
		try:
			canonical = canonicalize_cwd(cwd_raw)
		except CanonicalizationError:
			return JSONResponse({"state": "none"})
		session = registry.get_session(canonical)
		if session is None or len(session.agent_senders) < 2:
			return JSONResponse({"state": "none"})
		if session._waiting:
			return JSONResponse({"state": "blocked"})
		return JSONResponse({"state": "live"})
	return collab_partner_state


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
	# Stateful HTTP: session-scoped transport so per-tool-call cancel
	# notifications can find the in-flight responder. The cost is that an MCP
	# session does not survive a server restart — Claude Code (issue #27142,
	# closed not-planned) caches Mcp-Session-Id and gets a 404, then drops the
	# tool list permanently. Workaround: /exit and relaunch CC after a server
	# restart. Acceptable here because restarts are rare in normal use.
	#
	# session_idle_timeout is left at the default (None) so a long-blocking
	# `ask_human` awaiting John's reply for up to 24h is not reaped mid-call.
	mcp = FastMCP("switchboard", stateless_http=False)

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
		Omit message on your first call if you are Agent 2.
		Markdown is supported and is the default for collab messages."""
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


async def _wire_away_mode_mirror(registry: "Registry", backend: "AwayModeMirror") -> None:
	"""Register a callback that mirrors away-mode mutations to Firebase.
	The callback writes the (cwd, active) pair as-is; the listener path
	(start_away_mode_listeners, wired in Task 8) is responsible for updating
	the in-memory cache."""
	loop = asyncio.get_running_loop()

	def _on_change(cwd: "str | None", active: bool | None) -> None:
		scope = "global" if cwd is None else cwd
		_spawn_bg(
			backend.write_away_mode_mirror(cwd, active),
			label=f"away_mode_mirror:{scope}",
		)

	registry.set_away_mode_callback(_on_change)


async def _run(config: Config) -> None:
	logger = JsonlLogger(config.log_path)
	registry = Registry()

	if not (config.firebase_service_account_json and config.firebase_database_url):
		raise ConfigError(
			"Firebase is required. Set FIREBASE_SERVICE_ACCOUNT_JSON and FIREBASE_DATABASE_URL "
			"as OS env vars or in .env."
		)

	backend = FirebaseBackend(
		service_account_json=config.firebase_service_account_json,
		database_url=config.firebase_database_url,
		storage_bucket=config.firebase_storage_bucket,
		logger=logger,
	)

	sidecar_path = _Path(config.log_path).parent / "collab-sessions.json"
	await _notify_lost_collab_sessions(sidecar_path, backend)

	await _wire_away_mode_mirror(registry, backend)

	# Reset away-mode state BEFORE loading the snapshot. In stateful HTTP mode
	# (which we use to get cancel-notification propagation), a server restart
	# invalidates every pre-existing CC session and those agents lose access to
	# switchboard tools. Leaving away_mode=true would trap them in a Stop-hook
	# loop ("call ask_human" → tool unavailable → repeat). Resetting to off
	# lets them gracefully fall back to terminal output. The user re-enables
	# away mode via spawn (per-channel) or the phone (global).
	await backend.reset_all_away_mode()
	# Populate cache from Firebase (now reflects the post-reset state), start
	# listeners, zero pending counters.
	await backend.load_away_mode_snapshot(registry)
	await backend.delete_legacy_away_mode_node()
	await backend.start_away_mode_listeners(registry)
	await backend.reset_all_pending_responses()

	mirror_writer_fn = getattr(backend, "make_pending_mirror_writer", None)
	if callable(mirror_writer_fn):
		registry.set_pending_mirror(mirror_writer_fn())

	limiter = RateLimiter(config.rate_limit)
	handlers = build_tool_handlers(config, registry, backend, logger, limiter)
	loop_sups = {
		"dispatch_responses": LoopSupervisor("dispatch_responses", backend, logger.surface_error),
		"dispatch_commands": LoopSupervisor("dispatch_commands", backend, logger.surface_error),
		"dispatch_inject_queue": LoopSupervisor("dispatch_inject_queue", backend, logger.surface_error),
		"dispatch_away_mode_commands": LoopSupervisor("dispatch_away_mode_commands", backend, logger.surface_error),
	}
	mcp = _build_fastmcp(handlers)

	app = mcp.streamable_http_app()

	async def healthz(request: Request):
		listeners = []
		listener_health_fn = getattr(backend, "listener_health", None)
		if callable(listener_health_fn):
			try:
				listeners = listener_health_fn()
			except Exception as exc:
				await logger.surface_error(f"healthz_listener_health_error: {exc}")
		import time as _time
		now = _time.monotonic()
		dispatch_loops = []
		for sup in loop_sups.values():
			h = sup.health()
			dispatch_loops.append({
				"name": h.name,
				"consecutive_failures": h.consecutive_failures,
				"crash_count": h.crash_count,
				"last_crash_seconds_ago": (
					(now - h.last_crash_at) if h.last_crash_at is not None else None
				),
			})
		dispatch_loops.sort(key=lambda d: d["name"])
		return JSONResponse({
			"pending": {
				"count": registry.pending_count,
				"oldest_pending_age_seconds": registry.oldest_pending_age_seconds,
				"total_answered": registry.total_answered,
			},
			"listeners": listeners,
			"dispatch_loops": dispatch_loops,
		})

	app.add_route("/healthz", healthz, methods=["GET"])
	app.add_route("/away-mode", _build_away_mode_route(registry), methods=["GET"])
	app.add_route("/collab-partner-state", _build_collab_partner_state_route(registry), methods=["GET"])

	uv_config = uvicorn.Config(
		app,
		host=config.host,
		port=config.port,
		log_level="info",
	)
	server = uvicorn.Server(uv_config)

	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, loop_sups["dispatch_responses"])
	)

	spawn_handler = SpawnHandler(config, backend, logger, registry)
	spawn_task = asyncio.create_task(
		dispatch_commands(spawn_handler, backend, logger, loop_sups["dispatch_commands"])
	)

	inject_task = asyncio.create_task(
		dispatch_inject_queue(registry, backend, logger, loop_sups["dispatch_inject_queue"])
	)

	away_cmd_task = asyncio.create_task(
		dispatch_away_mode_commands(registry, backend, logger, loop_sups["dispatch_away_mode_commands"])
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
