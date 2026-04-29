from __future__ import annotations

import asyncio
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
from server.gateway.document import _validate_path, _sha256_hex
from server.gateway.bg_tasks import _spawn_bg

TIMEOUT_SENTINEL = "__TIMEOUT__"

_SESSION_START = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]

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

@dataclass
class ToolHandlers:
	ask_human: Callable[..., Coroutine[None, None, str]]
	notify_human: Callable[..., Coroutine[None, None, str]]
	send_document_human: Callable[..., Coroutine[None, None, str]]
	message_and_await_agent: Callable[..., Coroutine[None, None, str]]
	end_collab: Callable[..., Coroutine[None, None, str]]
	enter_away_mode: Callable[..., Coroutine[None, None, str]]
	exit_away_mode: Callable[..., Coroutine[None, None, str]]

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
			_spawn_bg(
				backend.write_session_meta(canonical, "collab", canonical, agent_senders=[], task=""),
				label=f"collab_session_meta:{canonical}",
			)
			_spawn_bg(
				_write_byo_sidecar(config.log_path, canonical),
				label=f"byo_sidecar:{canonical}",
			)
			_spawn_bg(
				backend.start_inject_listener(canonical),
				label=f"inject_listener:{canonical}",
			)

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
				_spawn_bg(_relay(), label=f"collab_relay:{channel_id}:{actual_sender}")

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

	return ToolHandlers(
		ask_human=ask_human,
		notify_human=notify_human,
		send_document_human=send_document_human,
		message_and_await_agent=message_and_await_agent,
		end_collab=end_collab,
		enter_away_mode=enter_away_mode,
		exit_away_mode=exit_away_mode,
	)
