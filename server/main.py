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

import dataclasses

from server.config import Config, ConfigError, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_responses,
)
from server.gateway.dispatch import (
	dispatch_combine_commands,
	dispatch_force_end_commands,
	dispatch_spawn_commands,
	dispatch_away_mode_commands,
)
from server.gateway.bg_tasks import _spawn_bg
from server.hydration import hydrate_from_firebase
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.rate_limiter import RateLimiter
from server.firebase import FirebaseBackend
from server.firebase_supervisor import LoopSupervisor


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
		return JSONResponse({"active": bool(registry.global_away_mode)})
	return away_mode


def _build_cli_session_end_route(registry, backend=None):
	"""POST /cli-session/end — SessionEnd hook posts here to mark the matching
	conversation member dormant. Best-effort: returns 200 even on bad input or
	unknown session so the hook never blocks shutdown."""
	from datetime import datetime, timezone
	from server.cli_session_end import handle_session_end

	async def cli_session_end(request: Request):
		try:
			body = await request.json()
		except Exception:
			return JSONResponse({}, status_code=200)
		session_id = body.get("session_id")
		reason = body.get("reason", "other")
		if not isinstance(session_id, str) or not session_id:
			return JSONResponse({}, status_code=200)
		try:
			await handle_session_end(
				registry=registry,
				session_id=session_id,
				reason=reason if isinstance(reason, str) else "other",
				now=lambda: datetime.now(timezone.utc).isoformat(),
				backend=backend,
			)
		except Exception:
			# Don't surface server errors to the hook — best-effort.
			pass
		return JSONResponse({}, status_code=200)
	return cli_session_end


def _build_agent_status_route(handlers):
	"""POST /agent_status — hook-driven status writes. Always returns 200 with
	empty body, even on malformed input or backend failure (the handler swallows
	exceptions internally). The Firebase write is awaited directly: it's a
	~100ms operation, well inside the hook's 1-second timeout, and direct await
	avoids the test-loop complications of background-spawned tasks."""
	async def agent_status(request: Request):
		try:
			body = await request.json()
		except Exception:
			return JSONResponse({}, status_code=200)
		cwd = body.get("cwd")
		state = body.get("state")
		detail = body.get("detail")
		session_id = body.get("session_id")
		if not isinstance(cwd, str) or not cwd or not isinstance(state, str) or not state:
			return JSONResponse({}, status_code=200)
		await handlers.handle_agent_status(cwd, state, detail, session_id=session_id if isinstance(session_id, str) else None)
		return JSONResponse({}, status_code=200)
	return agent_status



def _build_fastmcp(handlers, host: str = "127.0.0.1") -> FastMCP:
	# Stateful HTTP: session-scoped transport so per-tool-call cancel
	# notifications can find the in-flight responder. The cost is that an MCP
	# session does not survive a server restart — Claude Code (issue #27142,
	# closed not-planned) caches Mcp-Session-Id and gets a 404, then drops the
	# tool list permanently. Workaround: /exit and relaunch CC after a server
	# restart. Acceptable here because restarts are rare in normal use.
	#
	# session_idle_timeout is left at the default (None) so a long-blocking
	# `ask_human` awaiting John's reply for up to 24h is not reaped mid-call.
	#
	# Pass through the host the uvicorn server will actually bind to. FastMCP
	# auto-enables DNS-rebinding protection (TrustedHostMiddleware with a
	# localhost-only allowlist) when its host parameter is 127.0.0.1, localhost,
	# or ::1 — independent of where uvicorn binds. Without passing host here,
	# FastMCP defaulted to 127.0.0.1 while uvicorn bound 0.0.0.0, and any
	# non-localhost client (e.g. WSL agents reaching us via the Windows host IP)
	# got 421 Invalid Host header on /mcp.
	mcp = FastMCP("switchboard", stateless_http=False, host=host)

	@mcp.tool()
	async def ask_human(
		question: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Block until John responds from his phone. Returns the response text,
		or '__TIMEOUT__' if the timeout window elapses.

		sender: your display name (kebab-case recommended).
		title: optional session label shown on John's phone tab.
		format: 'plain' (default) or 'markdown'.
		suggestions: optional list of quick-reply options.

		cli_session_id and cwd are injected automatically by the switchboard
		PreToolUse hook. Agents should not pass them."""
		return await handlers.ask_human(
			question, sender, title=title, format=format, suggestions=suggestions,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def notify_human(
		message: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Fire a status message to John. Non-blocking.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.notify_human(
			message, sender, title=title, format=format,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def send_document_human(
		path: str,
		sender: str,
		title: str | None = None,
		caption: str | None = None,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Deliver a file to John. Non-blocking. path is relative to cwd. Max 5 MB.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.send_document_human(
			path, sender, title=title, caption=caption,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def message_and_await_agent(
		sender: str,
		message: str,
		title: str | None = None,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Send a message to your collab partners and block until one of them speaks.
		Returns the talking-stick payload (delta since you last saw the conversation,
		excluding your own messages).

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.message_and_await_agent(
			sender, message, title=title,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def open_conversation(
		sender: str,
		title: str | None = None,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Promote your current conversation to be the globally open one. Other
		agents calling enter_conversation() will join it. Replaces any prior open
		marker. Non-blocking.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.open_conversation(
			sender, title=title,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def enter_conversation(
		sender: str,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Join the open conversation (or queue for intro in your current one).
		Blocks until you receive the conversation's payload via the talking-stick.

		Five behaviors depending on caller's state and the open pointer:
		- bound + open is yours OR no open exists: queue for intro in current
		- unbound + open exists: join open, queue for intro (full history)
		- bound + open != current: migrate from current to open, queue for intro
		- unbound + no open: error
		- bound + no open: queue for intro in current

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.enter_conversation(
			sender,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def combine_conversations(
		source_id: str,
		target_id: str,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Move all movable members of source_id into target_id, then end source_id.
		Permanently-lost members stay in source. Alive members are rebound immediately;
		dormant members are queued for launcher resume into target. Non-blocking.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.combine_conversations(
			source_id, target_id,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def lookup_conversation_ids(
		cwd_filter: str | None = None,
		sender_contains: str | None = None,
		title_contains: str | None = None,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Returns a JSON-encoded list of active conversation_ids matching ALL
		provided filters. At least one filter required.

		cwd_filter: exact case-insensitive match against members' cwd.
		sender_contains: case-insensitive substring match.
		title_contains: case-insensitive substring match.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.lookup_conversation_ids(
			cwd_filter=cwd_filter,
			sender_contains=sender_contains,
			title_contains=title_contains,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def leave_conversation(
		sender: str,
		parting_message: str,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Leave the conversation this session is bound to. parting_message is required.
		Appends the parting to the log, wakes blocked peers, applies session-fallback
		(rebind home if away, else unbind). Conversation ends if you were the last
		alive member and no dormant members remain.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.leave_conversation(
			sender, parting_message,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def set_away_mode(
		value: bool,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Set the global away_mode flag. Persisted to Firebase.

		cli_session_id and cwd are injected by the PreToolUse hook."""
		return await handlers.set_away_mode(
			value,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	return mcp



async def resolve_wsl_home() -> str | None:
	"""Resolve the WSL user's home path by running `echo $HOME` inside WSL.
	Returns None if WSL is unavailable, the command fails, or output is empty."""
	try:
		proc = await asyncio.create_subprocess_exec(
			"wsl.exe", "-e", "bash", "-lc", "echo $HOME",
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		stdout, _ = await proc.communicate()
		if proc.returncode != 0:
			return None
		result = stdout.decode("utf-8", errors="replace").strip()
		return result or None
	except Exception:
		return None


async def _run(config: Config) -> None:
	logger = JsonlLogger(config.log_path)
	registry = Registry()

	# Resolve WSL home at startup so downstream code can compute WSL paths
	# without spawning subprocesses per-request. Config is frozen; use replace().
	wsl_home = await resolve_wsl_home()
	config = dataclasses.replace(config, wsl_home_resolved=wsl_home)

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

	# Reset away-mode state BEFORE loading the snapshot. In stateful HTTP mode
	# (which we use to get cancel-notification propagation), a server restart
	# invalidates every pre-existing CC session and those agents lose access to
	# switchboard tools. Leaving away_mode=true would trap them in a Stop-hook
	# loop ("call ask_human" → tool unavailable → repeat). Resetting to off
	# lets them gracefully fall back to terminal output. The user re-enables
	# away mode from the phone (via /away_mode_commands) or from a spawned
	# agent (via the set_away_mode MCP tool).
	await backend.reset_all_away_mode()
	# Populate cache from Firebase (now reflects the post-reset state), start
	# listeners, zero pending counters.
	await backend.load_away_mode_snapshot(registry)
	await backend.delete_legacy_away_mode_node()
	await backend.start_away_mode_listeners(registry)
	await backend.reset_all_pending_responses()
	await backend.start_conversation_answers_listener()

	await hydrate_from_firebase(registry, backend, logger)

	try:
		await backend.set_global_wsl_available(bool(config.wsl_home_resolved))
	except Exception as exc:
		await logger.surface_error(f"set_global_wsl_available_failed: {exc}")

	mirror_writer_fn = getattr(backend, "make_pending_mirror_writer", None)
	if callable(mirror_writer_fn):
		registry.set_pending_mirror(mirror_writer_fn())

	limiter = RateLimiter(config.rate_limit)
	handlers = build_tool_handlers(config, registry, backend, logger, limiter)

	from server.spawn import SpawnHandler
	spawn_handler = SpawnHandler(config, backend, logger, registry)

	loop_sups = {
		"dispatch_responses": LoopSupervisor("dispatch_responses", backend, logger.surface_error),
		"dispatch_combine_commands": LoopSupervisor("dispatch_combine_commands", backend, logger.surface_error),
		"dispatch_force_end_commands": LoopSupervisor("dispatch_force_end_commands", backend, logger.surface_error),
		"dispatch_spawn_commands": LoopSupervisor("dispatch_spawn_commands", backend, logger.surface_error),
		"dispatch_away_mode_commands": LoopSupervisor("dispatch_away_mode_commands", backend, logger.surface_error),
	}
	mcp = _build_fastmcp(handlers, config.host)

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
	app.add_route("/agent_status", _build_agent_status_route(handlers), methods=["POST"])
	app.add_route("/cli-session/end", _build_cli_session_end_route(registry, backend=backend), methods=["POST"])

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

	combine_task = asyncio.create_task(
		dispatch_combine_commands(registry, backend, logger, loop_sups["dispatch_combine_commands"])
	)

	force_end_task = asyncio.create_task(
		dispatch_force_end_commands(registry, backend, logger, loop_sups["dispatch_force_end_commands"])
	)

	spawn_task = asyncio.create_task(
		dispatch_spawn_commands(spawn_handler, backend, logger, loop_sups["dispatch_spawn_commands"])
	)

	away_mode_task = asyncio.create_task(
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
		combine_task.cancel()
		force_end_task.cancel()
		spawn_task.cancel()
		away_mode_task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await dispatch_task
		with contextlib.suppress(asyncio.CancelledError):
			await combine_task
		with contextlib.suppress(asyncio.CancelledError):
			await force_end_task
		with contextlib.suppress(asyncio.CancelledError):
			await spawn_task
		with contextlib.suppress(asyncio.CancelledError):
			await away_mode_task
		await backend.aclose()


def run() -> None:
	config = load_config()
	try:
		asyncio.run(_run(config))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	run()
