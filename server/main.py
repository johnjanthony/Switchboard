"""Switchboard entry point — wires dependencies and runs the server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn
from mcp.server.fastmcp import FastMCP

from server.config import Config, load_config
from server.gateway import (
	build_tool_handlers,
	dispatch_commands,
	dispatch_responses,
)
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from server.spawn import SpawnHandler
from server.telegram import TelegramBackend


def _build_fastmcp(handlers) -> FastMCP:
	mcp = FastMCP("switchboard")

	@mcp.tool()
	async def ask_human(question: str, agent_id: str) -> str:
		"""Block until the developer responds from their phone. Returns
		the response text, or the sentinel '__TIMEOUT__' if the timeout
		window elapses."""
		return await handlers.ask_human(question, agent_id)

	@mcp.tool()
	async def notify_human(message: str, agent_id: str) -> str:
		"""Fire a status message to the developer. Non-blocking."""
		return await handlers.notify_human(message, agent_id)

	return mcp


async def _run(config: Config) -> None:
	# Silence httpx per-request INFO logs — they embed the bot token in the URL.
	logging.getLogger("httpx").setLevel(logging.WARNING)

	logger = JsonlLogger(config.log_path)
	registry = Registry()
	backend = TelegramBackend(
		token=config.telegram_bot_token,
		chat_id=config.telegram_chat_id,
		logger=logger,
	)

	# Preflight: verify token via getMe. Non-fatal per spec §7 — log and continue.
	try:
		await backend.preflight()
	except Exception as exc:
		logger.surface_error(f"telegram_preflight_failed: {exc}")

	handlers = build_tool_handlers(config, registry, backend, logger)
	mcp = _build_fastmcp(handlers)

	uv_config = uvicorn.Config(
		mcp.sse_app(),
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
