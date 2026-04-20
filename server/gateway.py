"""FastMCP tool handlers and response-dispatch loop.

`build_tool_handlers` returns a small object with the two tool coroutines
bound to the provided dependencies. `build_gateway` wires those into a
FastMCP instance. Keeping the handlers separable from the FastMCP wiring
makes them trivially unit-testable without spinning up an MCP server.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
from server.registry import Registry

TIMEOUT_SENTINEL = "__TIMEOUT__"


def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]


@dataclass
class ToolHandlers:
	ask_human: Callable[[str, str], Coroutine[None, None, str]]
	notify_human: Callable[[str, str], Coroutine[None, None, str]]


def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> ToolHandlers:
	async def notify_human(message: str, agent_id: str) -> str:
		try:
			await backend.send_notification(agent_id, message)
			logger.notify_sent(agent_id, message)
			return "ok"
		except Exception as exc:
			logger.tool_error(None, agent_id, str(exc))
			return f"ERROR: {exc}"

	async def ask_human(question: str, agent_id: str) -> str:
		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = None
		try:
			correlation = await backend.send_question(
				request_id, agent_id, question
			)
			future = registry.add(request_id, agent_id, correlation)
			logger.request_created(request_id, agent_id, question)
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

	return ToolHandlers(ask_human=ask_human, notify_human=notify_human)


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
