"""Switchboard entry point — wires dependencies and runs the server."""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
import os
import signal
from pathlib import Path as _Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

import dataclasses

from server.config import Config, ConfigError, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_responses,
)
from server.gateway.dispatch import (
	dispatch_combine_commands,
	dispatch_convene_commands,
	dispatch_force_end_commands,
	dispatch_spawn_commands,
	dispatch_away_mode_commands,
	dispatch_session_end_markers,
	dispatch_status_request_commands,
	dispatch_session_sweep,
	dispatch_conversation_sweep,
)
from server.gateway.bg_tasks import BG_FAILURE_AUDIT_LABEL, _spawn_bg, set_bg_failure_hook
from server.http_auth import TokenAuthMiddleware
from server.hydration import hydrate_from_firebase
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.session_registry import SessionRegistry
from server.rate_limiter import RateLimiter
from server.firebase import FirebaseBackend
from server.firebase_supervisor import LoopSupervisor
from server.rules_audit import audit_rtdb_rules
from server.widget_snapshot import WidgetSnapshotStore
from server.claude_status import ClaudeStatusService


def _build_away_mode_route(registry: Registry, session_registry, backend=None, logger=None):
	"""GET /away-mode - the turn-end hook's single check. With session_id, the
	response also delivers (and pops) any queued wake notices for that session;
	the hook blocks the turn with the notice text so the agent acts on it.
	POST /away-mode - set global away mode ON."""
	async def away_mode(request: Request):
		if request.method == "POST":
			try:
				body = await request.json()
			except Exception:
				body = {}
			active = body.get("active", body.get("away", True))
			if active:
				registry.global_away_mode = True
				if backend and hasattr(backend, "set_global_away_mode"):
					try:
						await backend.set_global_away_mode(True)
					except Exception as exc:
						if logger:
							await logger.surface_error(f"http_away_mode_enter_persist_failed: {exc}")
				if logger:
					await logger.info("http_away_mode_enter_global")
		notices: list = []
		session_id = request.query_params.get("session_id")
		if session_id:
			notices = session_registry.pop_notices(session_id)
		return JSONResponse({"active": bool(registry.global_away_mode), "notices": notices})
	return away_mode


def _build_session_start_route(session_registry: SessionRegistry, logger):
	"""POST /session_start — SessionStart hook ingest. Always 200 (hook contract)."""
	async def session_start(request: Request):
		try:
			body = await request.json()
		except Exception:
			return JSONResponse({}, status_code=200)
		session_id = body.get("session_id")
		if not isinstance(session_id, str) or not session_id:
			return JSONResponse({}, status_code=200)
		cwd = body.get("cwd") if isinstance(body.get("cwd"), str) else ""
		source = body.get("source") if isinstance(body.get("source"), str) else None
		if source == "resume":
			expected = session_registry.check_resume_id_change(session_id, cwd)
			if expected is not None:
				await logger.surface_error(
					f"resume_id_change_detected: spawn-resumed {expected} but SessionStart arrived as "
					f"{session_id} (cwd {cwd}) - CC id-on-resume behavior may have changed; see the "
					f"dormant supersession rules in the roadmap"
				)
		session_registry.record_session_start(session_id, cwd=cwd, start_source=source)
		return JSONResponse({}, status_code=200)
	return session_start


def _build_sessions_route(session_registry: SessionRegistry):
	"""GET /sessions - the roster as JSON (localhost trust, like /stats).
	cli_session_id is redacted to an 8-char prefix (REV-003): the full id is
	the forgeable routing identity, and this route's only consumer is a human
	debugging - the Firebase mirror keeps full ids for the phone surfaces."""
	def _redact(payload: dict) -> dict:
		sid = payload.get("cli_session_id") or ""
		if len(sid) > 8:
			payload["cli_session_id"] = sid[:8] + "..."
		return payload

	async def sessions(request: Request):
		return JSONResponse({"sessions": [_redact(r.to_payload()) for r in session_registry.snapshot()]})
	return sessions


def _build_agent_status_route(handlers, session_registry: SessionRegistry):
	"""POST /agent_status - hook-driven status writes. Returns 200 with an empty
	body on malformed input, or on success returns {"notices": [...]} - popped
	only for the UserPromptSubmit event, the only hook with a channel to deliver
	them to the agent. The Firebase write is awaited directly: it's a ~100ms
	operation, well inside the hook's 1-second timeout, and direct await avoids
	the test-loop complications of background-spawned tasks.

	The session-registry upsert happens BEFORE the away-mode gate inside
	handlers.handle_agent_status, so an unknown session is discovered in the
	roster even when the conversation-status write itself is gated off."""
	async def agent_status(request: Request):
		try:
			body = await request.json()
		except Exception:
			return JSONResponse({}, status_code=200)
		session_id = body.get("session_id")
		state = body.get("state")
		detail = body.get("detail")
		event = body.get("event")
		cwd = body.get("cwd") if isinstance(body.get("cwd"), str) else None
		cli = body.get("cli") if isinstance(body.get("cli"), str) else None
		if not isinstance(session_id, str) or not session_id or not isinstance(state, str) or not state:
			return JSONResponse({}, status_code=200)
		from server.session_registry import map_hook_event_to_state
		mapped = map_hook_event_to_state(event, state) if isinstance(event, str) else None
		if mapped is not None:
			in_tool: bool | None = None
			if event == "PreToolUse":
				in_tool = mapped == "active"
			elif event in ("PostToolUse", "UserPromptSubmit", "Stop"):
				in_tool = False
			session_registry.upsert_from_hook(
				session_id, state=mapped,
				detail=detail if isinstance(detail, str) else None,
				cwd=cwd, event=event, in_tool=in_tool, cli=cli,
			)
		else:
			session_registry.touch_mcp(session_id, cwd=cwd or "")
		await handlers.handle_agent_status(session_id, state, detail)
		notices: list = []
		if event == "UserPromptSubmit":
			notices = session_registry.pop_notices(session_id)
		return JSONResponse({"notices": notices}, status_code=200)
	return agent_status


def _compute_healthy(listeners, loop_failures) -> bool:
	"""The one health verdict shared by /stats and /healthz (and thus by the
	dashboard and the Watchtower widget). A listener is unhealthy iff its state
	is 'reconnecting' ('stopped' is an intentional shutdown, 'starting' is
	transient); a dispatch loop is unhealthy iff its consecutive_failures > 0.
	listeners: iterable of dicts with a 'state'; loop_failures: iterable of the
	per-loop consecutive_failures ints."""
	listener_unhealthy = any(
		isinstance(l, dict) and l.get("state") == "reconnecting" for l in listeners
	)
	loop_unhealthy = any((f or 0) > 0 for f in loop_failures)
	return not listener_unhealthy and not loop_unhealthy


def _build_stats_route(registry: Registry, backend, loop_sups: dict, session_registry=None):
	"""GET /stats - the widget-facing roll-up (localhost, unauthenticated, same
	trust model as /healthz and /away-mode). The widget polls this so it never
	needs Firebase.

	A listener is unhealthy iff its state
	is 'reconnecting' (per firebase_supervisor, that is the dead-and-retrying
	state; 'stopped' is an intentional shutdown and 'starting' is transient, so
	neither counts); a dispatch loop is unhealthy iff consecutive_failures > 0.
	/stats, /healthz, and the dashboard all read this one server-computed verdict
	(_compute_healthy), so they cannot disagree."""
	async def stats(request: Request):
		listeners = []
		listener_health_fn = getattr(backend, "listener_health", None)
		if callable(listener_health_fn):
			try:
				listeners = listener_health_fn()
			except Exception:
				listeners = []
		healthy = _compute_healthy(
			listeners,
			[sup.health().consecutive_failures for sup in loop_sups.values()],
		)
		return JSONResponse({
			"active_conversations": registry.active_conversations_count,
			"pending_count": registry.pending_count,
			"oldest_pending_age_seconds": registry.oldest_pending_age_seconds,
			"away_mode": bool(registry.global_away_mode),
			"healthy": healthy,
			"sessions": {
				"total": len(session_registry.snapshot()) if session_registry is not None else 0,
				"by_state": session_registry.counts_by_state() if session_registry is not None else {},
			},
		})
	return stats


def _with_route_limit(handler, limiter, path: str, logger):
	"""Coarse per-route token bucket for the unauthenticated POST routes
	(REV-109). Legitimate traffic is at most a few events per second (hooks +
	Watchtower), so the generous default (SWITCHBOARD_ROUTE_RATE_LIMIT, 600/min
	per route) never throttles it - this only stops a runaway or abusive flood
	from becoming unbounded RTDB writes. 429 is safe: all three HTTP hooks and
	Watchtower fail open on non-200."""
	async def limited(request: Request):
		if not limiter.consume(path):
			await logger.rate_limited(path, "http_route")
			return JSONResponse({"error": "rate limited"}, status_code=429)
		return await handler(request)
	return limited


def _build_widget_snapshot_route(store, backend, logger, session_registry=None):
	"""POST /widget-snapshot - Watchtower pushes its rings + quota snapshot here
	(localhost trust, same model as /stats and /agent_status). The store diffs
	against the last push so RTDB is written only on change; pushed_at is always
	written so readers can show staleness. Also feeds the session registry so a
	Watchtower ring can enrich or discover a session row."""
	_RING_FIELDS = ("pct", "model", "status", "context_tokens", "window", "is_error", "name", "name_source", "title_state")

	async def widget_snapshot(request: Request):
		try:
			body = await request.json()
		except Exception:
			return JSONResponse({"error": "invalid json"}, status_code=400)
		if not isinstance(body, dict):
			return JSONResponse({"error": "expected object"}, status_code=400)
		rings_list = body.get("rings")
		if not isinstance(rings_list, list):
			return JSONResponse({"error": "rings must be a list"}, status_code=400)
		quota = body.get("quota")
		pushed_at = body.get("pushed_at") or ""

		rings_map: dict = {}
		for r in rings_list:
			if isinstance(r, dict) and isinstance(r.get("session_id"), str):
				rings_map[r["session_id"]] = {k: r.get(k) for k in _RING_FIELDS}

		rings_changed, quota_changed = store.apply(rings_map, quota, pushed_at)
		if session_registry is not None:
			session_registry.apply_rings(rings_map)
		try:
			if rings_changed:
				await backend.write_widget_rings(rings_map)
			if quota_changed:
				await backend.write_widget_quota(quota)
			await backend.write_widget_pushed_at(pushed_at)
		except Exception as exc:
			await logger.surface_error(f"widget_snapshot_write_error: {exc}")
			return JSONResponse({"error": "write failed"}, status_code=502)
		return JSONResponse({"ok": True, "rings_changed": rings_changed, "quota_changed": quota_changed})

	return widget_snapshot


def _build_widget_status_route(service):
	"""POST /widget-status {action: check|stop} - the same-origin control surface
	Operator (and later Watchtower) use to drive the server-owned status watch.
	Localhost trust, like /widget-snapshot. Returns the current view."""
	async def widget_status(request: Request):
		if request.method == "GET":
			return JSONResponse(service.view())
		try:
			body = await request.json()
		except Exception:
			body = {}
		action = request.query_params.get("action") or (body.get("action") if isinstance(body, dict) else None)
		if action == "check":
			return JSONResponse(await service.check())
		if action == "stop":
			return JSONResponse(await service.stop())
		return JSONResponse({"error": "unknown action"}, status_code=400)

	return widget_status


def _build_document_route(backend):
	"""GET /document?conv=&msg=[&download=1] — same-origin proxy that streams a
	document message's bytes so the Operator preview page can render content
	without a cross-origin fetch. Localhost-trust, same model as /stats: it only
	serves blobs referenced by an existing document message."""
	from server.gateway.document import guess_content_type
	from starlette.responses import Response
	from urllib.parse import quote

	async def document(request: Request):
		conv = request.query_params.get("conv")
		msg = request.query_params.get("msg")
		if not conv or not msg:
			return Response("missing conv/msg", status_code=400)
		try:
			data, filename = await backend.read_document(conv, msg)
		except LookupError:
			return Response("not found", status_code=404)
		except Exception:
			return Response("download failed", status_code=502)
		disposition = "attachment" if request.query_params.get("download") else "inline"
		media_type = guess_content_type(filename)
		# An uploaded document must never execute on our own origin (the dashboard
		# is served from here too). HTML/SVG/XML render as active content, so force
		# them to download as opaque bytes; nosniff stops the browser from
		# reinterpreting any other type as one of these.
		executable_types = {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}
		if media_type in executable_types:
			disposition = "attachment"
			media_type = "application/octet-stream"
		return Response(
			data,
			media_type=media_type,
			headers={
				"Content-Disposition": f'{disposition}; filename="{quote(filename)}"',
				"X-Content-Type-Options": "nosniff",
			},
		)

	return document


KEEPALIVE_INTERVAL_SECONDS = 60.0


async def _await_with_progress_keepalive(mcp: FastMCP, coro, interval: float = KEEPALIVE_INTERVAL_SECONDS):
	"""Await a blocking tool handler while pinging MCP progress notifications.

	Claude Code >= 2.1.187 aborts a remote MCP tool call that is silent (no
	response AND no progress notification) for 5 minutes (its
	CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT default). ask_human and the other
	human/agent-blocking tools legitimately sit silent for hours, so the server
	heartbeats while the handler waits. The keepalive is strictly best-effort:
	a missing progressToken (report_progress no-ops), a dead stream, or a
	context lookup failure must never change the handler's outcome.
	"""
	try:
		ctx = mcp.get_context()
	except Exception:
		# No request context (or SDK change): the tool still works, it just
		# loses idle-abort protection. Log so the regression is visible.
		logging.getLogger(__name__).exception("progress keepalive unavailable; awaiting handler without pings")
		return await coro
	task = asyncio.ensure_future(coro)
	beats = 0
	try:
		while True:
			done, _ = await asyncio.wait({task}, timeout=interval)
			if done:
				return task.result()
			beats += 1
			try:
				await ctx.report_progress(beats, None, "waiting")
			except Exception:
				# Dead stream or transport hiccup: keep waiting; resolution
				# still lands in Firebase even if this stream never hears it.
				pass
	except asyncio.CancelledError:
		# Propagate the client's cancel INTO the handler so its own
		# CancelledError cleanup (e.g. ask_human marking the question
		# cancelled) runs before we re-raise.
		task.cancel()
		with contextlib.suppress(asyncio.CancelledError, Exception):
			await task
		raise


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
		or one-line JSON {"status":"timeout"} / {"status":"conversation_ended",...}
		when no reply can arrive.

		sender: your display name (kebab-case recommended).
		title: optional session label shown on John's phone tab.
		format: 'plain' (default) or 'markdown'.
		suggestions: optional quick-reply options. MUST be a JSON array of
		strings, e.g. ["Yes", "No", "Ship it"]; any other shape is rejected.

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
		# Keepalive: this call legitimately blocks for hours awaiting John.
		return await _await_with_progress_keepalive(mcp, handlers.ask_human(
			question, sender, title=title, format=format, suggestions=suggestions,
			cli_session_id=cli_session_id, cwd=cwd,
		))

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

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
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

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
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
		Returns one-line JSON: {"status":"ok","log":...} on a peer wake,
		{"status":"timeout"}, or {"status":"conversation_ended",...}.

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
		# Keepalive: blocks until a collab partner speaks, which can be hours.
		return await _await_with_progress_keepalive(mcp, handlers.message_and_await_agent(
			sender, message, title=title,
			cli_session_id=cli_session_id, cwd=cwd,
		))

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
		Returns one-line JSON: {"status":"ok","source":...,"target":...,"detail":...}
		on success; an "ERROR: ..." string on failure.

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
		return await handlers.combine_conversations(
			source_id, target_id,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	@mcp.tool()
	async def join_conversation(
		sender: str,
		ref: str | None = None,
		title: str | None = None,
		cli_session_id: str | None = None,
		cwd: str | None = None,
	) -> str:
		"""Join a conversation. Never blocks; idempotent.

		ref: a conversation_id (from lookup_conversation_ids, a convene notice,
		or John's prompt) to join that conversation - migrating you out of your
		current one if needed. Omit ref to join the currently-open conversation,
		or mint a fresh one (promoted as open) when none exists.

		Returns one-line JSON: {"status":"ok", "conversation_id", "sender",
		"peers", "log"?, "minted"?, "already_member"?}. "log" is the history you
		have not seen yet (full on first join). To wait for peers afterwards,
		call message_and_await_agent.

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
		return await handlers.join_conversation(
			sender, ref=ref, title=title,
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
		"""Returns one-line JSON: {"status":"ok","conversation_ids":[...]} - the active
		conversation_ids matching ALL provided filters. At least one filter required.

		cwd_filter: exact case-insensitive match against members' cwd.
		sender_contains: case-insensitive substring match.
		title_contains: case-insensitive substring match.

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
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
		Returns one-line JSON: {"status":"ok","conversation_id":...}.

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
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

		cli_session_id and cwd identify your session. Claude Code: injected
		automatically by the plugin hook (do not pass them). Other CLIs (e.g.
		Antigravity): pass cli_session_id=<your conversation id> and
		cwd=<your workspace root> explicitly on every call."""
		return await handlers.set_away_mode(
			value,
			cli_session_id=cli_session_id, cwd=cwd,
		)

	return mcp



async def resolve_wsl_home(logger=None) -> str | None:
	"""Resolve the WSL user's home path.

	Order of resolution:
	  1. SWITCHBOARD_WSL_HOME env var (escape hatch — used when the NSSM
	     service runs in Session 0 and `wsl.exe -e bash` fails to find a
	     usable distro for the service user).
	  2. `wsl.exe -e bash -lc "echo $HOME"` probe.

	Returns None if neither produces a result. Logs the failure cause via
	the optional logger (surface_error) so future "wsl_available=false"
	investigations have a breadcrumb.
	"""
	env_override = os.environ.get("SWITCHBOARD_WSL_HOME")
	if env_override:
		return env_override.strip() or None
	try:
		proc = await asyncio.create_subprocess_exec(
			"wsl.exe", "-e", "bash", "-lc", "echo $HOME",
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		stdout, stderr = await proc.communicate()
		if proc.returncode != 0:
			if logger is not None:
				err_text = stderr.decode("utf-8", errors="replace").strip()
				await logger.surface_error(
					f"resolve_wsl_home: wsl.exe exited {proc.returncode}: {err_text!r}"
				)
			return None
		result = stdout.decode("utf-8", errors="replace").strip()
		if not result and logger is not None:
			await logger.surface_error("resolve_wsl_home: wsl.exe returned empty stdout")
		return result or None
	except FileNotFoundError as exc:
		if logger is not None:
			await logger.surface_error(f"resolve_wsl_home: wsl.exe not on PATH ({exc})")
		return None
	except Exception as exc:
		if logger is not None:
			await logger.surface_error(f"resolve_wsl_home: unexpected error {exc!r}")
		return None


async def _run(config: Config) -> None:
	logger = JsonlLogger(config.log_path)

	# Route background-task failures into the JSONL audit log (REV-105): the
	# done-callback's stdlib logging reaches nssm-stderr only; this makes a
	# failed fire-and-forget write visible in logs/switchboard.jsonl too.
	def _bg_failure_audit(label: str, exc: BaseException) -> None:
		_spawn_bg(
			logger.surface_error(f"bg_task_failed:{label}: {exc!r}"),
			label=BG_FAILURE_AUDIT_LABEL,
		)
	set_bg_failure_hook(_bg_failure_audit)

	registry = Registry()
	session_registry = SessionRegistry()
	registry.sessions = session_registry
	widget_store = WidgetSnapshotStore()

	def _session_mirror(sid: str, payload: dict | None) -> None:
		if payload is None:
			_spawn_bg(backend.delete_session_record(sid), label=f"fb_session_delete:{sid}")
		else:
			_spawn_bg(backend.write_session_record(sid, payload), label=f"fb_session_write:{sid}")
	session_registry.set_mirror(_session_mirror)

	# Resolve WSL home at startup so downstream code can compute WSL paths
	# without spawning subprocesses per-request. Config is frozen; use replace().
	# Pass the logger so probe failures surface (Session 0 wsl.exe quirks etc.).
	wsl_home = await resolve_wsl_home(logger=logger)
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
	await backend.delete_open_conversation_node()
	await backend.start_away_mode_listeners(registry)
	await backend.reset_all_pending_responses()
	await backend.start_conversation_answers_listener()

	# Pending mirror wired BEFORE hydration: parked-pending rebuilds fire +1
	# per record, restoring each conversation's pending_responses counter
	# (zeroed just above) so the phone's reply input survives the restart.
	mirror_writer_fn = getattr(backend, "make_pending_mirror_writer", None)
	if callable(mirror_writer_fn):
		registry.set_pending_mirror(mirror_writer_fn())

	await hydrate_from_firebase(registry, backend, logger, session_registry=session_registry)

	try:
		await backend.set_global_wsl_available(bool(config.wsl_home_resolved))
	except Exception as exc:
		await logger.surface_error(f"set_global_wsl_available_failed: {exc}")

	# REV-004: verify the DEPLOYED RTDB rules are real (not placeholder or
	# test-mode) - the whole phone command channel rests on them. Loud but
	# non-fatal by design (see server/rules_audit.py).
	await audit_rtdb_rules(backend, logger)

	limiter = RateLimiter(config.rate_limit)
	route_limiter = RateLimiter(config.route_rate_limit)
	handlers = build_tool_handlers(config, registry, backend, logger, limiter, session_registry=session_registry)

	from server.spawn import SpawnHandler
	spawn_handler = SpawnHandler(config, backend, logger, registry)

	loop_sups = {
		"dispatch_responses": LoopSupervisor("dispatch_responses", backend, logger.surface_error),
		"dispatch_combine_commands": LoopSupervisor("dispatch_combine_commands", backend, logger.surface_error),
		"dispatch_convene_commands": LoopSupervisor("dispatch_convene_commands", backend, logger.surface_error),
		"dispatch_force_end_commands": LoopSupervisor("dispatch_force_end_commands", backend, logger.surface_error),
		"dispatch_spawn_commands": LoopSupervisor("dispatch_spawn_commands", backend, logger.surface_error),
		"dispatch_away_mode_commands": LoopSupervisor("dispatch_away_mode_commands", backend, logger.surface_error),
		"dispatch_session_end_markers": LoopSupervisor("dispatch_session_end_markers", backend, logger.surface_error),
		"dispatch_status_request_commands": LoopSupervisor("dispatch_status_request_commands", backend, logger.surface_error),
		"dispatch_session_sweep": LoopSupervisor("dispatch_session_sweep", backend, logger.surface_error),
		"dispatch_conversation_sweep": LoopSupervisor("dispatch_conversation_sweep", backend, logger.surface_error),
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
		healthy = _compute_healthy(
			listeners,
			[sup.health().consecutive_failures for sup in loop_sups.values()],
		)
		return JSONResponse({
			"pending": {
				"count": registry.pending_count,
				"parked": registry.parked_count,
				"oldest_pending_age_seconds": registry.oldest_pending_age_seconds,
				"total_answered": registry.total_answered,
			},
			"healthy": healthy,
			"listeners": listeners,
			"dispatch_loops": dispatch_loops,
		})

	claude_status_service = ClaudeStatusService(publish=backend.write_widget_status)
	app.add_route("/healthz", healthz, methods=["GET"])
	app.add_route(
		"/widget-snapshot",
		_with_route_limit(
			_build_widget_snapshot_route(widget_store, backend, logger, session_registry),
			route_limiter, "/widget-snapshot", logger,
		),
		methods=["POST"],
	)
	app.add_route("/widget-status", _build_widget_status_route(claude_status_service), methods=["GET", "POST"])
	app.add_route("/away-mode", _build_away_mode_route(registry, session_registry, backend, logger), methods=["GET", "POST"])
	app.add_route(
		"/stats",
		_build_stats_route(registry, backend, loop_sups, session_registry=session_registry),
		methods=["GET"],
	)
	app.add_route("/document", _build_document_route(backend), methods=["GET"])
	app.add_route(
		"/session_start",
		_with_route_limit(
			_build_session_start_route(session_registry, logger), route_limiter, "/session_start", logger,
		),
		methods=["POST"],
	)
	app.add_route("/sessions", _build_sessions_route(session_registry), methods=["GET"])
	app.add_route(
		"/agent_status",
		_with_route_limit(
			_build_agent_status_route(handlers, session_registry), route_limiter, "/agent_status", logger,
		),
		methods=["POST"],
	)
	dashboard_dir = _Path(__file__).resolve().parent.parent / "dashboard"
	app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")

	uv_config = uvicorn.Config(
		TokenAuthMiddleware(app, token=config.auth_token),
		host=config.host,
		port=config.port,
		log_level="info",
	)
	server = uvicorn.Server(uv_config)

	session_end_marker_dir = _Path(config.log_path).parent / "session-end"
	session_end_marker_dir.mkdir(parents=True, exist_ok=True)
	await logger.info(f"session_end_marker_dir: {session_end_marker_dir}")

	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, loop_sups["dispatch_responses"], session_registry=session_registry)
	)

	combine_task = asyncio.create_task(
		dispatch_combine_commands(
			registry, backend, logger, loop_sups["dispatch_combine_commands"],
			pending_dir=_Path(config.log_path).parent,
		)
	)

	convene_task = asyncio.create_task(
		dispatch_convene_commands(
			registry, session_registry, backend, logger, loop_sups["dispatch_convene_commands"], spawn_handler,
		)
	)

	force_end_task = asyncio.create_task(
		dispatch_force_end_commands(registry, backend, logger, loop_sups["dispatch_force_end_commands"])
	)

	spawn_task = asyncio.create_task(
		dispatch_spawn_commands(spawn_handler, backend, logger, loop_sups["dispatch_spawn_commands"])
	)

	away_mode_task = asyncio.create_task(
		dispatch_away_mode_commands(registry, backend, logger, loop_sups["dispatch_away_mode_commands"], session_registry=session_registry)
	)

	status_request_task = asyncio.create_task(
		dispatch_status_request_commands(
			claude_status_service, backend, logger, loop_sups["dispatch_status_request_commands"]
		)
	)

	session_end_markers_task = asyncio.create_task(
		dispatch_session_end_markers(
			registry, backend, logger, loop_sups["dispatch_session_end_markers"],
			session_end_marker_dir, session_registry=session_registry,
		)
	)

	session_sweep_task = asyncio.create_task(
		dispatch_session_sweep(
			session_registry, widget_store, logger, loop_sups["dispatch_session_sweep"],
			lost_after_seconds=config.session_lost_after_seconds,
			retention_hours=config.session_retention_hours,
			registry=registry, backend=backend, marker_dir=session_end_marker_dir,
		)
	)

	conversation_sweep_task = asyncio.create_task(
		dispatch_conversation_sweep(
			registry, backend, logger, loop_sups["dispatch_conversation_sweep"],
			retention_hours=config.conversation_retention_hours,
			admin_retention_hours=config.admin_notification_retention_hours,
		)
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
		convene_task.cancel()
		force_end_task.cancel()
		spawn_task.cancel()
		away_mode_task.cancel()
		session_end_markers_task.cancel()
		status_request_task.cancel()
		session_sweep_task.cancel()
		conversation_sweep_task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await dispatch_task
		with contextlib.suppress(asyncio.CancelledError):
			await combine_task
		with contextlib.suppress(asyncio.CancelledError):
			await convene_task
		with contextlib.suppress(asyncio.CancelledError):
			await force_end_task
		with contextlib.suppress(asyncio.CancelledError):
			await spawn_task
		with contextlib.suppress(asyncio.CancelledError):
			await away_mode_task
		with contextlib.suppress(asyncio.CancelledError):
			await session_end_markers_task
		with contextlib.suppress(asyncio.CancelledError):
			await status_request_task
		with contextlib.suppress(asyncio.CancelledError):
			await session_sweep_task
		with contextlib.suppress(asyncio.CancelledError):
			await conversation_sweep_task
		# Flush outstanding fire-and-forget background writes (member removals,
		# answer-history writes, pending-question cleanups, etc.) before the loop
		# closes, so a clean shutdown doesn't drop them. Bounded so a stuck write
		# can't hang shutdown.
		from server.gateway.bg_tasks import drain_bg_tasks
		await drain_bg_tasks(timeout=5.0)
		await backend.aclose()


def run() -> None:
	config = load_config()
	try:
		asyncio.run(_run(config))
	except KeyboardInterrupt:
		pass


if __name__ == "__main__":
	run()
