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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
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
		raise ValueError(f"Absolute paths are not allowed: {path_str}")

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


def _append_session_log(log_path: str, agent_id: str, direction: str, text: str) -> None:
	path = Path(log_path).parent / "sessions" / f"{agent_id}_{_SESSION_START}.log"
	path.parent.mkdir(exist_ok=True)
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	with path.open("a", encoding="utf-8") as f:
		f.write(f"{ts} {direction} {text}\n")


@dataclass
class ToolHandlers:
	ask_human: Callable[..., Coroutine[None, None, str]]
	notify_human: Callable[..., Coroutine[None, None, str]]
	send_document_human: Callable[..., Coroutine[None, None, str]]


def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> ToolHandlers:
	async def notify_human(message: str, agent_id: str, format: str = "plain") -> str:
		try:
			await backend.send_notification(agent_id, message, format)
			logger.notify_sent(agent_id, message)
			return "ok"
		except Exception as exc:
			logger.tool_error(None, agent_id, str(exc))
			return f"ERROR: {exc}"

	async def ask_human(
		question: str,
		agent_id: str,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> str:
		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = None
		try:
			correlation = await backend.send_question(
				request_id, agent_id, question, format, suggestions
			)
			future = registry.add(request_id, agent_id, correlation)
			logger.request_created(request_id, agent_id, question)
			_append_session_log(config.log_path, agent_id, "→", question)
		except Exception as exc:
			logger.tool_error(request_id, agent_id, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(
				future, timeout=config.timeout_seconds
			)
		except asyncio.TimeoutError:
			logger.timeout(request_id, agent_id, config.timeout_seconds)
			registry.remove(request_id)
			try:
				await backend.send_timeout_followup(
					request_id,
					agent_id,
					config.timeout_seconds,
					correlation,
				)
			except Exception as exc:
				logger.surface_error(
					f"timeout_followup_failed: {exc}",
					correlation=str(correlation),
				)
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			registry.remove(request_id)
			raise
		except Exception as exc:
			logger.tool_error(request_id, agent_id, str(exc))
			registry.remove(request_id)
			return f"ERROR: {exc}"

		_append_session_log(config.log_path, agent_id, "←", result)
		duration_ms = int(
			(datetime.now(timezone.utc) - started).total_seconds() * 1000
		)
		logger.request_resolved(
			request_id,
			agent_id,
			response_text=result,
			source="telegram",
			duration_ms=duration_ms,
		)
		try:
			await backend.send_resolution_confirmation(
				request_id, agent_id, correlation
			)
		except Exception as exc:
			logger.surface_error(
				f"resolution_confirmation_failed: {exc}",
				correlation=str(correlation),
			)
		return result

	async def send_document_human(
		path: str, agent_id: str, caption: str | None = None, *, cwd: Path | None = None
	) -> str:
		try:
			resolved = _validate_path(path, cwd=cwd)
		except ValueError as exc:
			logger.tool_error(None, agent_id, str(exc))
			return f"ERROR: {exc}"

		try:
			size_bytes = resolved.stat().st_size
			sha256 = _sha256_hex(resolved)
			await backend.send_document(agent_id, resolved, caption)
		except Exception as exc:
			logger.tool_error(None, agent_id, str(exc))
			return f"ERROR: {exc}"

		try:
			logger.document_sent(agent_id, str(resolved), size_bytes, sha256, caption)
		except Exception as exc:
			logger.surface_error(f"document_audit_failed: {exc}")
		return "ok"

	return ToolHandlers(
		ask_human=ask_human,
		notify_human=notify_human,
		send_document_human=send_document_human,
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
					request_id = registry.resolve_by_correlation(
						response.correlation, response.text
					)
					if request_id is None:
						logger.surface_error(
							"unknown_correlation",
							correlation=str(response.correlation),
						)
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
	async for raw in backend.poll_commands():
		try:
			await spawn_handler.handle(raw)
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			logger.surface_error(f"dispatch_commands_error: {exc}")
