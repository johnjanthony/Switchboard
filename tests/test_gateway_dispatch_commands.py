"""Tests for the dispatch_commands coroutine."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.logging_jsonl import JsonlLogger


@pytest.fixture
def logger(tmp_path):
	return JsonlLogger(str(tmp_path / "log.jsonl"))


@pytest.mark.asyncio
async def test_dispatch_commands_routes_to_handler(logger):
	from server.gateway import dispatch_commands

	received: list[str] = []

	async def fake_poll_commands():
		yield "/spawn rpdm/next-gen do stuff"
		await asyncio.Event().wait()  # block so the outer while True can be cancelled

	spawn_handler = MagicMock()
	spawn_handler.handle = AsyncMock(side_effect=lambda raw: received.append(raw) or None)

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	task = asyncio.create_task(dispatch_commands(spawn_handler, backend, logger))
	await asyncio.sleep(0)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	assert received == ["/spawn rpdm/next-gen do stuff"]


@pytest.mark.asyncio
async def test_dispatch_commands_continues_after_handler_exception(logger):
	from server.gateway import dispatch_commands

	call_count = 0

	async def fake_poll_commands():
		yield "/spawn first"
		yield "/spawn second"
		await asyncio.Event().wait()  # block after all items so we can cancel cleanly

	async def flaky_handle(raw: str) -> None:
		nonlocal call_count
		call_count += 1
		if call_count == 1:
			raise RuntimeError("handler boom")

	spawn_handler = MagicMock()
	spawn_handler.handle = flaky_handle

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	task = asyncio.create_task(dispatch_commands(spawn_handler, backend, logger))
	# Sleep 50ms — long enough for the to_thread-based async logger writes
	# (now triggered on the exception path) to complete and the loop to
	# advance to the second item.
	await asyncio.sleep(0.05)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	assert call_count == 2


@pytest.mark.asyncio
async def test_dispatch_commands_logs_handler_exception(tmp_path, logger):
	from server.gateway import dispatch_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))

	async def fake_poll_commands():
		yield "/spawn first"
		await asyncio.Event().wait()  # block after item so we can cancel cleanly

	async def always_fails(raw: str) -> None:
		raise RuntimeError("handler boom")

	spawn_handler = MagicMock()
	spawn_handler.handle = always_fails

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	task = asyncio.create_task(dispatch_commands(spawn_handler, backend, logger))
	# Sleep 50ms — long enough for the to_thread-based async logger
	# write to flush to disk before we read the log file.
	await asyncio.sleep(0.05)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert len(errors) == 1
	assert "handler boom" in errors[0]["detail"]


@pytest.mark.asyncio
async def test_dispatch_commands_restarts_after_generator_crash(tmp_path):
	"""If poll_commands() itself raises, the loop restarts after a short sleep."""
	from server.gateway import dispatch_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))

	call_count = 0

	async def fake_poll_commands():
		nonlocal call_count
		call_count += 1
		if call_count == 1:
			raise RuntimeError("connection lost")
		# Second call: block indefinitely so the test can cancel the task
		await asyncio.Event().wait()
		yield  # unreachable; presence marks this function as an async generator

	spawn_handler = MagicMock()
	spawn_handler.handle = AsyncMock()

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	task = asyncio.create_task(dispatch_commands(spawn_handler, backend, logger))
	# Let the crash + asyncio.sleep(1.0) elapse, then the second poll_commands call starts
	await asyncio.sleep(1.1)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	assert call_count == 2
	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert any("connection lost" in e["detail"] for e in errors)
