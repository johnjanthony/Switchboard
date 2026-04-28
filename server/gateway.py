"""FastMCP tool handlers and response-dispatch loop.

`build_tool_handlers` returns a small object with the two tool coroutines
bound to the provided dependencies. `build_gateway` wires those into a
FastMCP instance. Keeping the handlers separable from the FastMCP wiring
makes them trivially unit-testable without spinning up an MCP server.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from server.canonicalization import CanonicalizationError, canonicalize_cwd
from server.collab import CollabSession
from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
from server.rate_limiter import RateLimiter
from server.registry import Registry
from server.title_tracker import TitleTracker

TIMEOUT_SENTINEL = "__TIMEOUT__"

_SESSION_START = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_DENYLIST_EXACT = frozenset({".env", "service-account.json"})
_DENYLIST_GLOBS = ("*token*", "*secret*", "*.pem", "*.key", ".env*", "*.env")


def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]


async def _sha256_hex(path: Path) -> str:
	def _do_sha256():
		h = hashlib.sha256()
		with path.open("rb") as f:
			for chunk in iter(lambda: f.read(65536), b""):
				h.update(chunk)
		return h.hexdigest()
	return await asyncio.to_thread(_do_sha256)


def _validate_path(path_str: str, cwd: Path | None = None) -> Path:
	"""Return the resolved Path if safe; raise ValueError otherwise."""
	p = Path(path_str)
	if p.is_absolute():
		resolved = p.resolve()
	else:
		_cwd = (cwd or Path.cwd()).resolve()
		resolved = (_cwd / p).resolve()
		try:
			resolved.relative_to(_cwd)
		except ValueError:
			raise ValueError(f"Path escapes project directory: {path_str}")

	if not resolved.exists():
		raise ValueError(f"File not found: {path_str}")
	if not resolved.is_file():
		raise ValueError(f"Not a file: {path_str}")

	size = resolved.stat().st_size
	if size > _MAX_DOCUMENT_BYTES:
		raise ValueError(f"File too large ({size} bytes, max {_MAX_DOCUMENT_BYTES})")

	name_lower = resolved.name.lower()
	if name_lower in _DENYLIST_EXACT:
		raise ValueError(f"File is on the deny list: {resolved.name}")
	for pattern in _DENYLIST_GLOBS:
		if fnmatch.fnmatch(name_lower, pattern):
			raise ValueError(
				f"File matches restricted pattern '{pattern}': {resolved.name}"
			)

	return resolved


async def _append_session_log(log_path: str, channel_id: str, direction: str, text: str, logger: JsonlLogger) -> None:
	from server.canonicalization import to_firebase_key
	key = to_firebase_key(channel_id).replace(":", "_")
	path = Path(log_path).parent / "sessions" / f"{key}_{_SESSION_START}.log"
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	line = f"{ts} {direction} {text}\n"

	await logger.info(f"writing_session_log: {path.absolute()}")

	def _write() -> None:
		path.parent.mkdir(exist_ok=True)
		with path.open("a", encoding="utf-8") as f:
			f.write(line)

	await asyncio.to_thread(_write)


async def _write_byo_sidecar(log_path: str, channel_id: str) -> None:
	sidecar_path = Path(log_path).parent / "collab-sessions.json"
	entry = {
		"channel_id": channel_id,
		"agent_senders": [],
		"task": "",
		"created_at": datetime.now(timezone.utc).isoformat(),
	}

	def _write() -> None:
		existing: list = []
		if sidecar_path.exists():
			try:
				existing = json.loads(sidecar_path.read_text(encoding="utf-8"))
			except Exception:
				pass
		existing.append(entry)
		sidecar_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

	await asyncio.to_thread(_write)


async def _safe_mark_cancelled(backend: MessengerBackend, cwd: str, request_id: str, logger: JsonlLogger) -> None:
	try:
		await backend.mark_question_cancelled(cwd, request_id)
	except Exception as exc:
		await logger.surface_error(f"mark_cancelled_failed: {exc}")


_LOOP_CRASH_ALERT_THRESHOLD = 5
_LOOP_BACKOFF_MAX = 60.0


async def _loop_crash_backoff(
	backend: Any,
	logger: JsonlLogger,
	label: str,
	consecutive_failures: int,
	backoff: float,
	exc: Exception,
) -> float:
	"""Log a dispatch-loop crash, escalate to the admin channel after the
	threshold is hit, sleep `backoff` seconds, and return the next backoff."""
	await logger.surface_error(
		f"{label}_loop_crashed: {exc} (count={consecutive_failures}, sleep={backoff:.1f}s)"
	)
	if consecutive_failures == _LOOP_CRASH_ALERT_THRESHOLD:
		try:
			await backend.send_text(
				f"Switchboard {label} loop has failed {consecutive_failures} times in a row — check service logs."
			)
		except Exception as alert_exc:
			await logger.surface_error(f"{label}_loop_alert_failed: {alert_exc}")
	await asyncio.sleep(backoff)
	return min(backoff * 2, _LOOP_BACKOFF_MAX)


@dataclass
class ToolHandlers:
	ask_human: Callable[..., Coroutine[None, None, str]]
	notify_human: Callable[..., Coroutine[None, None, str]]
	send_document_human: Callable[..., Coroutine[None, None, str]]
	message_and_await_agent: Callable[..., Coroutine[None, None, str]]
	end_collab: Callable[..., Coroutine[None, None, str]]
	enter_away_mode: Callable[..., Coroutine[None, None, str]]
	exit_away_mode: Callable[..., Coroutine[None, None, str]]
	build_bulk_respond_payload: Callable[..., Coroutine[None, None, dict]]
	bulk_respond_send_to_all: Callable[..., Coroutine[None, None, None]]
	bulk_respond_skip: Callable[..., Coroutine[None, None, None]]
	bulk_respond_cancel: Callable[..., Coroutine[None, None, None]]


def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
	limiter: RateLimiter | None = None,
) -> ToolHandlers:
	title_tracker = TitleTracker()

	def _validate_sender(sender: str) -> str | None:
		if "__" in sender:
			return f"ERROR: sender name '{sender}' contains restricted characters '__'."
		return None

	async def notify_human(
		message: str,
		cwd: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
	) -> str:
		if err := _validate_sender(sender):
			return err
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"
		if limiter is not None and not limiter.consume(canonical):
			await logger.rate_limited(canonical, "notify_human")
			return (
				f"ERROR: rate limit exceeded — you are sending too fast.\n"
				f"Limit is {limiter.rate_per_minute} messages/min per channel.\n"
				f"Wait at least {limiter.wait_seconds} seconds before retrying, or slow your notify cadence."
			)
		try:
			await backend.write_channel_message(canonical, sender, "notify", message, format=format, title=title)
			await logger.notify_sent(canonical, message)
			await _append_session_log(config.log_path, canonical, "→", message, logger)
			return "ok"
		except Exception as exc:
			await logger.tool_error(None, canonical, str(exc))
			return f"ERROR: {exc}"

	async def ask_human(
		question: str,
		cwd: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> str:
		if err := _validate_sender(sender):
			return err
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"

		if not registry.is_away_mode_active(canonical):
			# At-desk redirect: deliver the question as a notify (downgrade
			# message_type, drop request_id + suggestions), skip PendingRequest
			# registration, and return a redirect-error so the SKILL steers the
			# agent to produce the question in the terminal instead.
			try:
				await backend.write_channel_message(
					canonical, sender, "notify", question, format=format, title=title,
				)
				await logger.notify_sent(canonical, question)
				await _append_session_log(config.log_path, canonical, "→", question, logger)
			except Exception as exc:
				await logger.tool_error(None, canonical, str(exc))
				return f"ERROR: {exc}"
			return "ERROR: John is at his desk. Ask this question via the terminal."

		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = None
		try:
			correlation, msg_id = await backend.write_channel_message(
				canonical, sender, "question", question,
				request_id=request_id, format=format, suggestions=suggestions, title=title,
			)
			future, prior_request_id = registry.add(
				cwd=canonical, sender=sender, request_id=request_id, msg_id=msg_id, return_superseded=True,
			)
			if prior_request_id is not None:
				await _safe_mark_cancelled(backend, canonical, prior_request_id, logger)
			await logger.request_created(request_id, canonical, question)
			await _append_session_log(config.log_path, canonical, "→", question, logger)
		except asyncio.CancelledError:
			await _safe_mark_cancelled(backend, canonical, request_id, logger)
			registry.remove(canonical, sender)
			raise
		except Exception as exc:
			await logger.tool_error(request_id, canonical, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
		except asyncio.TimeoutError:
			await logger.timeout(request_id, canonical, config.timeout_seconds)
			registry.remove(canonical, sender)
			try:
				await backend.send_timeout_followup(
					request_id, canonical, config.timeout_seconds, correlation,
				)
			except Exception as exc:
				await logger.surface_error(f"timeout_followup_failed: {exc}", correlation=str(correlation))
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			await _safe_mark_cancelled(backend, canonical, request_id, logger)
			registry.remove(canonical, sender)
			raise
		except Exception as exc:
			await logger.tool_error(request_id, canonical, str(exc))
			registry.remove(canonical, sender)
			return f"ERROR: {exc}"

		await _append_session_log(config.log_path, canonical, "←", result, logger)
		duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
		source = "unknown"
		if isinstance(correlation, dict):
			source = "multi"
		elif str(correlation).startswith("firebase_"):
			source = "firebase"
		elif str(correlation).startswith("android_"):
			source = "android_rest"
		await logger.request_resolved(request_id, canonical, response_text=result, source=source, duration_ms=duration_ms)
		try:
			await backend.send_resolution_confirmation(request_id, canonical, correlation, response_text=result)
		except Exception as exc:
			await logger.surface_error(f"resolution_confirmation_failed: {exc}", correlation=str(correlation))
		return result

	async def send_document_human(
		path: str,
		cwd: str,
		sender: str,
		title: str | None = None,
		caption: str | None = None,
		*,
		_cwd_path: Path | None = None,
	) -> str:
		if err := _validate_sender(sender):
			return err
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"
		cwd_path = _cwd_path if _cwd_path is not None else Path(canonical)
		try:
			resolved = _validate_path(path, cwd=cwd_path)
		except ValueError as exc:
			await logger.tool_error(None, canonical, str(exc))
			return f"ERROR: {exc}"

		if limiter is not None and not limiter.consume(canonical):
			await logger.rate_limited(canonical, "send_document_human")
			return (
				f"ERROR: rate limit exceeded — you are sending too fast.\n"
				f"Limit is {limiter.rate_per_minute} messages/min per channel.\n"
				f"Wait at least {limiter.wait_seconds} seconds before retrying, or slow your notify cadence."
			)

		try:
			size_bytes = resolved.stat().st_size
			sha256 = await _sha256_hex(resolved)
			await backend.write_channel_message(
				canonical, sender, "document",
				caption or resolved.name,
				url=str(resolved),
				filename=resolved.name,
				title=title,
			)
		except Exception as exc:
			await logger.tool_error(None, canonical, str(exc))
			return f"ERROR: {exc}"

		try:
			await logger.document_sent(canonical, str(resolved), size_bytes, sha256, caption)
		except Exception as exc:
			await logger.surface_error(f"document_audit_failed: {exc}")
		entry = f"[document: {resolved.name}] {caption}" if caption else f"[document: {resolved.name}]"
		await _append_session_log(config.log_path, canonical, "→", entry, logger)
		return "ok"

	async def message_and_await_agent(
		cwd: str,
		sender: str,
		title: str | None = None,
		message: str | None = None,
	) -> str:
		if err := _validate_sender(sender):
			return err
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"
		channel_id = canonical
		session = registry.get_session(canonical)
		if session is None:
			session = CollabSession(
				cwd=canonical, agent_senders=[], task="", is_byo=True
			)
			registry.add_session(session)
			asyncio.create_task(backend.write_session_meta(
				canonical, "collab", canonical, agent_senders=[], task=""
			))
			asyncio.create_task(_write_byo_sidecar(config.log_path, canonical))
			asyncio.create_task(backend.start_inject_listener(canonical))

		err = session.enroll(sender)
		if err == "duplicate":
			return f"ERROR: sender '{sender}' is already enrolled — use a unique sender name"
		if err == "full":
			return "ERROR: session is full"

		try:
			if message is not None:
				await logger.collab_message_sent(channel_id, sender, message)
				await _append_session_log(config.log_path, channel_id, "→", f"{sender}: {message}", logger)

			deliveries = session.handle_message(sender, message, title, title_tracker)
			for actual_sender, payload in deliveries:
				# Capture locals for the async task
				async def _relay(cid=channel_id, s=actual_sender, msg=payload) -> None:
					try:
						await backend.write_channel_message(cid, s, "agent", msg, format="markdown")
					except Exception as exc:
						await logger.surface_error(f"collab_relay_error: {exc}")
				asyncio.create_task(_relay())

			future = session.start_waiting(sender)
		except Exception as exc:
			await logger.tool_error(None, sender, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
			await logger.collab_message_received(channel_id, sender, result)
			await _append_session_log(config.log_path, channel_id, "←", f"{sender}: {result}", logger)
			return result
		except asyncio.TimeoutError:
			session.cancel_waiting(sender)
			await logger.surface_error(f"collab_timeout: channel={channel_id} sender={sender}")
			try:
				await backend.write_channel_message(
					channel_id, "system", "notify",
					f"Collab session `{channel_id}` — `{sender}` timed out after 24h.",
					format="markdown",
				)
			except Exception as exc:
				await logger.surface_error(f"collab_timeout_notify_error: {exc}")
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			session.cancel_waiting(sender)
			raise

	async def end_collab(
		cwd: str,
		sender: str,
		message: str | None = None,
		hand_off_to_human: bool = True,
	) -> str:
		if err := _validate_sender(sender):
			return err
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"

		session = registry.get_session(canonical)
		if session is None:
			# If the session is gone, check if it was recently ended AND if this
			# sender was actually a member of that session. We store the members
			# in the recently_ended breadcrumb to prevent strangers from getting
			# the "already ended" reporter-status message.
			ended_members = registry.get_recently_ended_members(canonical)
			if ended_members is not None:
				if sender in ended_members:
					return "ok. You are NOT the reporter; partner already ended. Collab closed."
			return "ERROR: not a member of this session"

		if sender not in session.agent_senders:
			return "ERROR: not a member of this session"

		if session.has_pending_inject():
			return (
				"ERROR: human inject queue is non-empty. Drain pending injects via "
				"message_and_await_agent before ending collab."
			)

		sentinel = "__COLLAB_ENDED__"
		if message:
			sentinel = f"__COLLAB_ENDED__\n{message}"

		# Resolve any partner futures BEFORE removing the session so in-flight
		# message_and_await_agent calls receive the sentinel deterministically.
		session.terminate(sentinel)
		registry.mark_session_ended(canonical, list(session.agent_senders))
		registry.remove_session(canonical)

		await logger.collab_message_sent(
			canonical, sender,
			f"[end_collab hand_off_to_human={hand_off_to_human}] {message or ''}",
		)
		await _append_session_log(
			config.log_path, canonical, "→",
			f"{sender}: [end_collab hand_off_to_human={hand_off_to_human}] {message or ''}",
			logger,
		)

		if hand_off_to_human:
			return "ok. You are the designated reporter. Report consensus to John."
		return "ok. Collab ended. Partner is reporting."

	async def enter_away_mode(cwd: str) -> str:
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"
		try:
			registry.set_cwd_override(canonical, True)
			await logger.away_mode_cwd_changed(canonical, True)
			return "ok"
		except Exception as exc:
			await logger.tool_error(None, canonical, str(exc))
			return f"ERROR: {exc}"

	async def exit_away_mode(cwd: str) -> str:
		try:
			canonical = canonicalize_cwd(cwd)
		except CanonicalizationError as exc:
			return f"ERROR: invalid cwd: {exc}"
		try:
			registry.set_cwd_override(canonical, False)
			await logger.away_mode_cwd_changed(canonical, False)
			return "ok"
		except Exception as exc:
			await logger.tool_error(None, canonical, str(exc))
			return f"ERROR: {exc}"

	async def build_bulk_respond_payload() -> dict:
		pending = registry.all_pending()
		groups: dict[str, list] = {}
		for p in pending:
			groups.setdefault(p.cwd, []).append(p)
		
		# Parallelize fetching question text for all pending requests
		async def _fetch_entry(p):
			question_text = ""
			if p.msg_id:
				try:
					question_text = await backend.fetch_message_text(p.cwd, p.msg_id) or ""
				except Exception:
					pass
			return {
				"cwd": p.cwd,
				"request_id": p.request_id,
				"sender": p.sender,
				"question_text": question_text,
			}

		results = await asyncio.gather(*[_fetch_entry(p) for p in pending])
		
		# Regroup by CWD for the payload sections
		sections_map: dict[str, list] = {}
		for res in results:
			cwd = res.pop("cwd")
			sections_map.setdefault(cwd, []).append(res)
		
		sections = [{"cwd": cwd, "entries": sections_map[cwd]} for cwd in sorted(sections_map.keys())]
		return {"sections": sections, "default_text": "Caught up — back at my desk."}

	async def bulk_respond_send_to_all(default_text: str) -> None:
		pending = registry.all_pending()
		
		async def _resolve_one(p):
			# Wrap the whole body so attribute-access failures during task construction
			# (e.g. backend missing write_channel_message) don't propagate and abort the fan-out.
			try:
				req_id = registry.resolve(cwd=p.cwd, sender=p.sender, text=default_text)
				if req_id is None:
					return
				tasks = [
					backend.send_resolution_confirmation(req_id, p.cwd, (p.cwd, p.sender), response_text=default_text),
				]
				if p.msg_id and hasattr(backend, "write_response_text"):
					tasks.append(backend.write_response_text(p.cwd, p.msg_id, default_text))
				tasks.append(backend.write_channel_message(p.cwd, "John", "human", default_text))
				await asyncio.gather(*tasks)
				await _append_session_log(config.log_path, p.cwd, "←", default_text, logger)
				await logger.notify_sent(p.cwd, f"Bulk Reply: {default_text}")
			except Exception as exc:
				await logger.surface_error(f"bulk_resolve_failed: cwd={p.cwd} sender={p.sender} err={exc}")

		if pending:
			await asyncio.gather(*[_resolve_one(p) for p in pending])

	async def bulk_respond_skip() -> None:
		pass

	async def bulk_respond_cancel() -> None:
		registry.set_global_away(True)

	return ToolHandlers(
		ask_human=ask_human,
		notify_human=notify_human,
		send_document_human=send_document_human,
		message_and_await_agent=message_and_await_agent,
		end_collab=end_collab,
		enter_away_mode=enter_away_mode,
		exit_away_mode=exit_away_mode,
		build_bulk_respond_payload=build_bulk_respond_payload,
		bulk_respond_send_to_all=bulk_respond_send_to_all,
		bulk_respond_skip=bulk_respond_skip,
		bulk_respond_cancel=bulk_respond_cancel,
	)


async def dispatch_responses(
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> None:
	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for response in backend.poll_responses():
				consecutive_failures = 0
				backoff = 1.0
				try:
					corr = response.correlation
					if isinstance(corr, tuple) and len(corr) == 2:
						cwd, sender = corr
						record = registry.get((cwd, sender))
						req_id = registry.resolve(cwd=cwd, sender=sender, text=response.text)
						if req_id is None:
							await logger.surface_error(f"unknown_correlation: cwd={cwd} sender={sender}")
							try:
								await backend.send_stale_reply_notice(cwd, sender)
							except Exception as exc:
								await logger.surface_error(f"stale_reply_notice_failed: {exc}")
						elif record is not None:
							if record.msg_id and hasattr(backend, "write_response_text"):
								# Update original question so it stays answered across restarts
								asyncio.create_task(backend.write_response_text(
									cwd, record.msg_id, response.text
								))
							# Add a NEW message to the history so it shows up in-line in the app
							async def _write_history(cid=cwd, txt=response.text):
								try:
									await backend.write_channel_message(cid, "John", "human", txt)
									await logger.notify_sent(cid, f"Reply: {txt}")
									await _append_session_log(logger.log_path, cid, "←", txt)
								except Exception as exc:
									await logger.surface_error(f"history_write_failed: {exc}")
							asyncio.create_task(_write_history())
					else:
						await logger.surface_error(f"legacy_correlation_dropped: {corr}")
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(
						f"dispatch_iteration_error: {exc}",
						correlation=str(response.correlation),
					)
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "dispatch_responses", consecutive_failures, backoff, exc,
			)


async def dispatch_commands(
	spawn_handler: Any,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for raw in backend.poll_commands():
				consecutive_failures = 0
				backoff = 1.0
				try:
					await spawn_handler.handle(raw)
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"dispatch_commands_error: {exc}")

		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "dispatch_commands", consecutive_failures, backoff, exc,
			)


async def dispatch_away_mode_commands(
	registry: Registry,
	backend: Any,
	handlers: "ToolHandlers",
	logger: JsonlLogger,
) -> None:
	"""Consume away_mode_commands queue entries and dispatch to registry/bulk-respond."""
	from server.canonicalization import canonicalize_cwd, CanonicalizationError

	poll = getattr(backend, "poll_away_mode_commands", None)
	if poll is None:
		return

	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for cmd in poll():
				consecutive_failures = 0
				backoff = 1.0
				cmd_type = cmd.get("type", "")
				try:
					if cmd_type == "enter_global":
						registry.set_global_away(True)
						await logger.info(f"away_mode_commands: enter_global applied")

					elif cmd_type == "exit_global":
						# User intent: exit global away. Honor unless the dialog flow returned an
						# explicit "cancel" decision. Default to "skip" (= exit, no replies) if any
						# step of the bulk-respond confirmation flow fails — partial failures must
						# not strand the user in away mode after they pressed exit.
						action = "skip"
						default_text = ""
						try:
							payload = await handlers.build_bulk_respond_payload()
							if payload["sections"]:
								default_text = payload.get("default_text", "")
								await backend.write_bulk_respond_dialog(payload)
								try:
									decision = await backend.poll_bulk_respond_decision()
									action = decision.get("action", "skip")
									default_text = decision.get("default_text", default_text)
								except Exception as exc:
									await logger.surface_error(f"away_mode_commands: exit_global decision poll error: {exc}")
								try:
									await backend.clear_bulk_respond_dialog()
								except Exception as exc:
									await logger.surface_error(f"away_mode_commands: clear_bulk_respond_dialog error: {exc}")
						except Exception as exc:
							await logger.surface_error(f"away_mode_commands: exit_global dialog setup error: {exc}")

						if action == "send_to_all":
							try:
								await handlers.bulk_respond_send_to_all(default_text)
							except Exception as exc:
								await logger.surface_error(f"away_mode_commands: bulk_respond_send_to_all error: {exc}")
							registry.set_global_away(False)
						elif action == "skip":
							registry.set_global_away(False)
						else:
							try:
								await handlers.bulk_respond_cancel()
							except Exception as exc:
								await logger.surface_error(f"away_mode_commands: bulk_respond_cancel error: {exc}")
						await logger.info(f"away_mode_commands: exit_global applied (action={action})")

					elif cmd_type == "enter_cwd":
						raw_cwd = cmd.get("cwd") or ""
						try:
							canonical = canonicalize_cwd(raw_cwd)
						except CanonicalizationError as exc:
							await logger.surface_error(f"away_mode_commands: enter_cwd bad cwd={raw_cwd!r} {exc}")
							continue
						registry.set_cwd_override(canonical, True)
						await logger.info(f"away_mode_commands: enter_cwd {canonical}")

					elif cmd_type == "exit_cwd":
						raw_cwd = cmd.get("cwd") or ""
						try:
							canonical = canonicalize_cwd(raw_cwd)
						except CanonicalizationError as exc:
							await logger.surface_error(f"away_mode_commands: exit_cwd bad cwd={raw_cwd!r} {exc}")
							continue
						registry.set_cwd_override(canonical, False)
						await logger.info(f"away_mode_commands: exit_cwd {canonical}")

					else:
						await logger.surface_error(f"away_mode_commands: unknown type={cmd_type!r}")

				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"away_mode_commands_dispatch_error: {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "away_mode_commands", consecutive_failures, backoff, exc,
			)


async def dispatch_inject_queue(
	registry: Registry,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	"""Deliver human inject messages from the Android compose box to collab sessions."""
	poll = getattr(backend, "poll_inject_messages", None)
	if poll is None:
		return
	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for session_id, inject_id, text in poll():
				consecutive_failures = 0
				backoff = 1.0
				try:
					session = registry.get_session(session_id)
					if session is None:
						await logger.surface_error(f"inject_unknown_session: {session_id} inject_id={inject_id}")
					else:
						session.deliver_inject(text)
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"inject_dispatch_error: inject_id={inject_id} {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "inject_queue", consecutive_failures, backoff, exc,
			)
