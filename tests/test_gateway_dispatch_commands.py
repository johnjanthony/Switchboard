"""Tests for the dispatch_commands coroutine."""

from __future__ import annotations

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

	spawn_handler = MagicMock()
	spawn_handler.handle = AsyncMock(side_effect=lambda raw: received.append(raw) or None)

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	await dispatch_commands(spawn_handler, backend, logger)

	assert received == ["/spawn rpdm/next-gen do stuff"]


@pytest.mark.asyncio
async def test_dispatch_commands_continues_after_handler_exception(logger):
	from server.gateway import dispatch_commands

	call_count = 0

	async def fake_poll_commands():
		yield "/spawn first"
		yield "/spawn second"

	async def flaky_handle(raw: str) -> None:
		nonlocal call_count
		call_count += 1
		if call_count == 1:
			raise RuntimeError("handler boom")

	spawn_handler = MagicMock()
	spawn_handler.handle = flaky_handle

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	await dispatch_commands(spawn_handler, backend, logger)

	assert call_count == 2


@pytest.mark.asyncio
async def test_dispatch_commands_logs_handler_exception(tmp_path, logger):
	from server.gateway import dispatch_commands

	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))

	async def fake_poll_commands():
		yield "/spawn first"

	async def always_fails(raw: str) -> None:
		raise RuntimeError("handler boom")

	spawn_handler = MagicMock()
	spawn_handler.handle = always_fails

	backend = MagicMock()
	backend.poll_commands = fake_poll_commands

	await dispatch_commands(spawn_handler, backend, logger)

	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	errors = [e for e in events if e["event"] == "surface_error"]
	assert len(errors) == 1
	assert "handler boom" in errors[0]["detail"]
