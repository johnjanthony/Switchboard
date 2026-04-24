"""Tests for enter_away_mode / exit_away_mode tool handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


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
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.enter_away_mode()
	assert result == "ok"
	assert registry.is_away_mode_active() is True


@pytest.mark.asyncio
async def test_enter_away_mode_logs_event(cfg, logger, tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	await handlers.enter_away_mode()
	events = [e for e in _log_events(cfg) if e["event"] == "away_mode_entered"]
	assert len(events) == 1
	assert "reason" not in events[0]


@pytest.mark.asyncio
async def test_exit_away_mode_clears_flag_and_returns_ok(cfg, logger, tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_away_mode(True)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.exit_away_mode()
	assert result == "ok"
	assert registry.is_away_mode_active() is False


@pytest.mark.asyncio
async def test_exit_away_mode_logs_event(cfg, logger, tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	registry.set_away_mode(True)
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	await handlers.exit_away_mode()
	events = [e for e in _log_events(cfg) if e["event"] == "away_mode_exited"]
	assert len(events) == 1


@pytest.mark.asyncio
async def test_enter_away_mode_is_idempotent(cfg, logger, tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	assert await handlers.enter_away_mode() == "ok"
	assert await handlers.enter_away_mode() == "ok"
	assert registry.is_away_mode_active() is True


@pytest.mark.asyncio
async def test_exit_away_mode_is_idempotent(cfg, logger, tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	assert await handlers.exit_away_mode() == "ok"
	assert await handlers.exit_away_mode() == "ok"
	assert registry.is_away_mode_active() is False


@pytest.mark.asyncio
async def test_enter_away_mode_error_returns_error_string(cfg, logger, tmp_path, monkeypatch):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")

	def boom(self, active):
		raise RuntimeError("set failed")

	monkeypatch.setattr(Registry, "set_away_mode", boom)

	handlers = build_tool_handlers(cfg, registry, RecordingBackend(), logger)
	result = await handlers.enter_away_mode()
	assert result.startswith("ERROR:")
	assert "set failed" in result
