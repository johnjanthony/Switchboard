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

from server.collab import CollabSession
from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
from server.rate_limiter import RateLimiter
from server.registry import Registry

TIMEOUT_SENTINEL = "__TIMEOUT__"

_SESSION_START = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_DENYLIST_EXACT = frozenset({".env", "service-account.json"})
_DENYLIST_GLOBS = ("*token*", "*secret*", "*.pem", "*.key", ".env*", "*.env")


def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]


def _sha256_hex(path: Path) -> str:
	h = hashlib.sha256()
	with path.open("rb") as f:
		for chunk in iter(lambda: f.read(65536), b""):
			h.update(chunk)
	return h.hexdigest()


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


async def _append_session_log(log_path: str, channel_id: str, direction: str, text: str) -> None:
	path = Path(log_path).parent / "sessions" / f"{channel_id}_{_SESSION_START}.log"
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	line = f"{ts} {direction} {text}\n"

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


@dataclass
class ToolHandlers:
	ask_human: Callable[..., Coroutine[None, None, str]]
	notify_human: Callable[..., Coroutine[None, None, str]]
	send_document_human: Callable[..., Coroutine[None, None, str]]
	message_and_await_agent: Callable[..., Coroutine[None, None, str]]
	enter_away_mode: Callable[..., Coroutine[None, None, str]]
	exit_away_mode: Callable[..., Coroutine[None, None, str]]


def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
	limiter: RateLimiter | None = None,
) -> ToolHandlers:
	async def notify_human(message: str, channel_id: str, sender: str = "Claude", format: str = "plain") -> str:
		if limiter is not None and not limiter.consume(channel_id):
			logger.rate_limited(channel_id, "notify_human")
			return (
				f"ERROR: rate limit exceeded — you are sending too fast.\n"
				f"Limit is {limiter.rate_per_minute} messages/min per channel.\n"
				f"Wait at least {limiter.wait_seconds} seconds before retrying, or slow your notify cadence."
			)
		try:
			await backend.write_channel_message(channel_id, sender, "notify", message, format=format)
			logger.notify_sent(channel_id, message)
			await _append_session_log(config.log_path, channel_id, "→", message)
			return "ok"
		except Exception as exc:
			logger.tool_error(None, channel_id, str(exc))
			return f"ERROR: {exc}"

	async def ask_human(
		question: str,
		channel_id: str,
		sender: str = "Claude",
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> str:
		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = None
		try:
			correlation, msg_id = await backend.write_channel_message(
				channel_id, sender, "question", question,
				request_id=request_id, format=format, suggestions=suggestions,
			)
			future = registry.add(request_id, channel_id, correlation, msg_id=msg_id)
			logger.request_created(request_id, channel_id, question)
			await _append_session_log(config.log_path, channel_id, "→", question)
		except asyncio.CancelledError:
			registry.remove(request_id)
			raise
		except Exception as exc:
			logger.tool_error(request_id, channel_id, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
		except asyncio.TimeoutError:
			logger.timeout(request_id, channel_id, config.timeout_seconds)
			registry.remove(request_id)
			try:
				await backend.send_timeout_followup(
					request_id, channel_id, config.timeout_seconds, correlation,
				)
			except Exception as exc:
				logger.surface_error(f"timeout_followup_failed: {exc}", correlation=str(correlation))
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			registry.remove(request_id)
			raise
		except Exception as exc:
			logger.tool_error(request_id, channel_id, str(exc))
			registry.remove(request_id)
			return f"ERROR: {exc}"

		await _append_session_log(config.log_path, channel_id, "←", result)
		duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
		source = "unknown"
		if isinstance(correlation, dict):
			source = "multi"
		elif str(correlation).startswith("firebase_"):
			source = "firebase"
		elif str(correlation).startswith("android_"):
			source = "android_rest"
		logger.request_resolved(request_id, channel_id, response_text=result, source=source, duration_ms=duration_ms)
		try:
			await backend.send_resolution_confirmation(request_id, channel_id, correlation, response_text=result)
		except Exception as exc:
			logger.surface_error(f"resolution_confirmation_failed: {exc}", correlation=str(correlation))
		return result

	async def send_document_human(
		path: str, channel_id: str, sender: str = "Claude",
		caption: str | None = None, *, cwd: Path | None = None
	) -> str:
		try:
			resolved = _validate_path(path, cwd=cwd)
		except ValueError as exc:
			logger.tool_error(None, channel_id, str(exc))
			return f"ERROR: {exc}"

		if limiter is not None and not limiter.consume(channel_id):
			logger.rate_limited(channel_id, "send_document_human")
			return (
				f"ERROR: rate limit exceeded — you are sending too fast.\n"
				f"Limit is {limiter.rate_per_minute} messages/min per channel.\n"
				f"Wait at least {limiter.wait_seconds} seconds before retrying, or slow your notify cadence."
			)

		try:
			size_bytes = resolved.stat().st_size
			sha256 = _sha256_hex(resolved)
			await backend.write_channel_message(
				channel_id, sender, "document",
				caption or resolved.name,
				url=str(resolved),
				filename=resolved.name,
			)
		except Exception as exc:
			logger.tool_error(None, channel_id, str(exc))
			return f"ERROR: {exc}"

		try:
			logger.document_sent(channel_id, str(resolved), size_bytes, sha256, caption)
		except Exception as exc:
			logger.surface_error(f"document_audit_failed: {exc}")
		entry = f"[document: {resolved.name}] {caption}" if caption else f"[document: {resolved.name}]"
		await _append_session_log(config.log_path, channel_id, "→", entry)
		return "ok"

	async def message_and_await_agent(
		channel_id: str, sender: str, message: str | None = None
	) -> str:
		session = registry.get_session(channel_id)
		if session is None:
			session = CollabSession(
				session_id=channel_id, agent_senders=[], task="", is_byo=True
			)
			registry.add_session(session)
			asyncio.create_task(backend.write_session_meta(
				channel_id, "collab", channel_id, agent_senders=[], task=""
			))
			asyncio.create_task(_write_byo_sidecar(config.log_path, channel_id))
			asyncio.create_task(backend.start_inject_listener(channel_id))

		err = session.enroll(sender)
		if err == "duplicate":
			return f"ERROR: sender '{sender}' is already enrolled — use a unique sender name"
		if err == "full":
			return "ERROR: session is full"

		try:
			if message is not None:
				entry = {
					"speaker": sender,
					"message": message,
					"timestamp": datetime.now(timezone.utc).isoformat(),
				}
				session.transcript.append(entry)
				logger.collab_message_sent(channel_id, sender, message)
				await _append_session_log(config.log_path, channel_id, "→", f"{sender}: {message}")

				if len(session.agent_senders) == 2:
					if session._pre_enroll_msg is not None:
						pre_msg = session._pre_enroll_msg
						session._pre_enroll_msg = None
						buf_sender = session.other_sender(sender)
						session.deliver(sender, pre_msg)
						async def _relay_buf(cid=channel_id, s=buf_sender, msg=pre_msg) -> None:
							try:
								await backend.write_channel_message(cid, s, "agent", msg)
							except Exception as exc:
								logger.surface_error(f"collab_relay_error: {exc}")
						asyncio.create_task(_relay_buf())
					other = session.other_sender(sender)
					session.deliver(other, message)
					async def _relay(cid=channel_id, s=sender, msg=message) -> None:
						try:
							await backend.write_channel_message(cid, s, "agent", msg)
						except Exception as exc:
							logger.surface_error(f"collab_relay_error: {exc}")
					asyncio.create_task(_relay())
				else:
					session._pre_enroll_msg = message
			else:
				if len(session.agent_senders) == 2 and session._pre_enroll_msg is not None:
					pre_msg = session._pre_enroll_msg
					session._pre_enroll_msg = None
					buf_sender = session.other_sender(sender)
					session.deliver(sender, pre_msg)
					async def _relay_buf2(cid=channel_id, s=buf_sender, msg=pre_msg) -> None:
						try:
							await backend.write_channel_message(cid, s, "agent", msg)
						except Exception as exc:
							logger.surface_error(f"collab_relay_error: {exc}")
					asyncio.create_task(_relay_buf2())

			future = session.start_waiting(sender)
		except Exception as exc:
			logger.tool_error(None, sender, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
			logger.collab_message_received(channel_id, sender, result)
			await _append_session_log(config.log_path, channel_id, "←", f"{sender}: {result}")
			return result
		except asyncio.TimeoutError:
			session.cancel_waiting(sender)
			logger.surface_error(f"collab_timeout: channel={channel_id} sender={sender}")
			try:
				await backend.write_channel_message(
					channel_id, "system", "notify",
					f"Collab session `{channel_id}` — `{sender}` timed out after 24h.",
					format="markdown",
				)
			except Exception as exc:
				logger.surface_error(f"collab_timeout_notify_error: {exc}")
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			session.cancel_waiting(sender)
			raise

	async def enter_away_mode() -> str:
		try:
			registry.set_away_mode(True)
			logger.away_mode_entered()
			return "ok"
		except Exception as exc:
			logger.tool_error(None, None, str(exc))
			return f"ERROR: {exc}"

	async def exit_away_mode() -> str:
		try:
			registry.set_away_mode(False)
			logger.away_mode_exited()
			return "ok"
		except Exception as exc:
			logger.tool_error(None, None, str(exc))
			return f"ERROR: {exc}"

	return ToolHandlers(
		ask_human=ask_human,
		notify_human=notify_human,
		send_document_human=send_document_human,
		message_and_await_agent=message_and_await_agent,
		enter_away_mode=enter_away_mode,
		exit_away_mode=exit_away_mode,
	)


async def dispatch_responses(
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> None:
	while True:
		try:
			async for response in backend.poll_responses():
				try:
					record = registry.resolve_by_correlation(
						response.correlation, response.text
					)
					if record is None:
						logger.surface_error(
							"unknown_correlation",
							correlation=str(response.correlation),
						)
					elif record.msg_id and hasattr(backend, "write_response_text"):
						# Update original question so it stays answered across restarts
						asyncio.create_task(backend.write_response_text(
							record.channel_id, record.msg_id, response.text
						))
						# Add a NEW message to the history so it shows up in-line in the app
						async def _write_history():
							try:
								await backend.write_channel_message(
									record.channel_id, "Human", "human", response.text
								)
								logger.notify_sent(record.channel_id, f"Reply: {response.text}")
							except Exception as exc:
								logger.surface_error(f"history_write_failed: {exc}")
						
						asyncio.create_task(_write_history())
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					logger.surface_error(
						f"dispatch_iteration_error: {exc}",
						correlation=str(response.correlation),
					)
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			logger.surface_error(f"dispatch_loop_crashed: {exc}")
			await asyncio.sleep(1.0)


async def dispatch_commands(
	spawn_handler: Any,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	while True:
		try:
			async for raw in backend.poll_commands():
				try:
					await spawn_handler.handle(raw)
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					logger.surface_error(f"dispatch_commands_error: {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			logger.surface_error(f"dispatch_commands_loop_crashed: {exc}")
			await asyncio.sleep(1.0)


async def dispatch_inject_queue(
	registry: Registry,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	"""Deliver human inject messages from the Android compose box to collab sessions."""
	poll = getattr(backend, "poll_inject_messages", None)
	if poll is None:
		return
	while True:
		try:
			async for session_id, inject_id, text in poll():
				try:
					session = registry.get_session(session_id)
					if session is None:
						logger.surface_error(f"inject_unknown_session: {session_id} inject_id={inject_id}")
					else:
						session.deliver_inject(text)
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					logger.surface_error(f"inject_dispatch_error: inject_id={inject_id} {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			logger.surface_error(f"inject_queue_crashed: {exc}")
			await asyncio.sleep(1.0)
