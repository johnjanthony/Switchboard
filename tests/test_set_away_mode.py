"""Tests for set_away_mode."""

from __future__ import annotations

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_away_mode_true(cfg, logger):
	"""set_away_mode(True) sets registry.global_away_mode to True."""
	backend = RecordingBackend()
	registry = Registry()
	assert registry.global_away_mode is False
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.set_away_mode(True, cli_session_id="s-1", cwd="C:/X")

	assert "ok" in result
	assert "True" in result
	assert registry.global_away_mode is True


@pytest.mark.asyncio
async def test_set_away_mode_false(cfg, logger):
	"""set_away_mode(False) sets registry.global_away_mode to False."""
	backend = RecordingBackend()
	registry = Registry()
	registry.global_away_mode = True
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.set_away_mode(False, cli_session_id="s-1", cwd="C:/X")

	assert "ok" in result
	assert "False" in result
	assert registry.global_away_mode is False


@pytest.mark.asyncio
async def test_set_away_mode_false_flips_flag_before_bulk_resolve(cfg, logger, monkeypatch):
	"""A2: the in-memory flag must flip to False BEFORE the bulk-resolve runs.
	Otherwise, during the bulk-resolve's awaits the flag is still True, so a
	concurrently-arriving ask_human passes the at-desk gate and registers a new
	pending the snapshot does not cover — stranding it until the 24h timeout."""
	import server.gateway.bulk_respond as bulk_mod

	backend = RecordingBackend()
	registry = Registry()
	registry.global_away_mode = True
	# A pending so the bulk-resolve branch actually executes.
	registry.add(conversation_id="conv-1", sender="Claude", request_id="r1", cli_session_id="s-1")

	seen = {}

	async def _spy(reg, be, log, decision, default_text, session_registry=None):
		seen["away_at_call"] = reg.global_away_mode
		return True

	monkeypatch.setattr(bulk_mod, "_apply_bulk_respond_decision", _spy)

	handlers = build_tool_handlers(cfg, registry, backend, logger)
	await handlers.set_away_mode(False, cli_session_id="s-2", cwd="C:/X")

	assert seen["away_at_call"] is False, "flag must be flipped to False before the bulk-resolve runs"
	assert registry.global_away_mode is False


@pytest.mark.asyncio
async def test_set_away_mode_idempotent(cfg, logger):
	"""Calling set_away_mode(True) twice keeps the flag True."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	await handlers.set_away_mode(True, cli_session_id="s-1", cwd="C:/X")
	result = await handlers.set_away_mode(True, cli_session_id="s-1", cwd="C:/X")

	assert "ok" in result
	assert registry.global_away_mode is True


@pytest.mark.asyncio
async def test_set_away_mode_invalid_type(cfg, logger):
	"""Passing a non-bool value returns an ERROR string."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.set_away_mode("yes", cli_session_id="s-1", cwd="C:/X")

	assert result.startswith("ERROR")
	assert "boolean" in result


@pytest.mark.asyncio
async def test_set_away_mode_missing_cli_session_id(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.set_away_mode(True)

	assert result.startswith("ERROR: cli_session_id required")
