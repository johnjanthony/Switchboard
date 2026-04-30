"""Tests for enter_away_mode / exit_away_mode tool handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_registry_with_loopback as _make_registry_with_loopback
from tests.test_gateway_notify_human import RecordingBackend

_CWD = "c:/work/sw"


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


def _log_events(cfg) -> list[dict]:
	path = Path(cfg.log_path)
	if not path.exists():
		return []
	return [
		json.loads(line)
		for line in path.read_text(encoding="utf-8").splitlines()
		if line
	]


@pytest.mark.asyncio
async def test_enter_away_mode_sets_flag_and_returns_ok(cfg, logger, tmp_path):
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.enter_away_mode(_CWD)
	assert result == "ok"
	assert registry.is_away_mode_active(_CWD) is True


@pytest.mark.asyncio
async def test_enter_away_mode_calls_set_cwd_override(cfg, logger, tmp_path):
	"""enter_away_mode must call set_cwd_override(canonical, True)."""
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	await handlers.enter_away_mode(_CWD)
	assert registry.cwd_overrides().get(_CWD) is True


@pytest.mark.asyncio
async def test_enter_away_mode_logs_event(cfg, logger, tmp_path):
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	await handlers.enter_away_mode(_CWD)
	events = [e for e in _log_events(cfg) if e["event"] == "away_mode_cwd_changed"]
	assert len(events) == 1
	assert events[0]["active"] is True


@pytest.mark.asyncio
async def test_exit_away_mode_clears_flag_and_returns_ok(cfg, logger, tmp_path):
	registry = _make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.exit_away_mode(_CWD)
	assert result == "ok"
	assert registry.is_away_mode_active(_CWD) is False


@pytest.mark.asyncio
async def test_exit_away_mode_calls_set_cwd_override_false(cfg, logger, tmp_path):
	"""exit_away_mode must call set_cwd_override(canonical, False)."""
	registry = _make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	await handlers.exit_away_mode(_CWD)
	assert registry.cwd_overrides().get(_CWD) is False


@pytest.mark.asyncio
async def test_exit_away_mode_logs_event(cfg, logger, tmp_path):
	registry = _make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	await handlers.exit_away_mode(_CWD)
	events = [e for e in _log_events(cfg) if e["event"] == "away_mode_cwd_changed"]
	assert len(events) == 1
	assert events[0]["active"] is False


@pytest.mark.asyncio
async def test_enter_away_mode_is_idempotent(cfg, logger, tmp_path):
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	assert await handlers.enter_away_mode(_CWD) == "ok"
	assert await handlers.enter_away_mode(_CWD) == "ok"
	assert registry.is_away_mode_active(_CWD) is True


@pytest.mark.asyncio
async def test_exit_away_mode_is_idempotent(cfg, logger, tmp_path):
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	assert await handlers.exit_away_mode(_CWD) == "ok"
	assert await handlers.exit_away_mode(_CWD) == "ok"
	assert registry.is_away_mode_active(_CWD) is False


@pytest.mark.asyncio
async def test_enter_away_mode_invalid_cwd_returns_error(cfg, logger, tmp_path):
	"""Non-absolute cwd returns an error string."""
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.enter_away_mode("not-a-path")
	assert result.startswith("ERROR: invalid cwd:")


@pytest.mark.asyncio
async def test_exit_away_mode_invalid_cwd_returns_error(cfg, logger, tmp_path):
	"""Non-absolute cwd returns an error string."""
	registry = _make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.exit_away_mode("not-a-path")
	assert result.startswith("ERROR: invalid cwd:")


@pytest.mark.asyncio
async def test_enter_away_mode_error_returns_error_string(cfg, logger, tmp_path, monkeypatch):
	registry = _make_registry_with_loopback()

	def boom(self, cwd, active):
		raise RuntimeError("set failed")

	monkeypatch.setattr(Registry, "set_cwd_override", boom)

	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.enter_away_mode(_CWD)
	assert result.startswith("ERROR:")
	assert "set failed" in result
