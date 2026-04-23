"""Tests for Gemini and Heterogeneous Collab Spawn."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_spawn_handler import make_config, make_backend, _pending_path

@pytest.fixture
def spawn_dirs(tmp_path):
	(tmp_path / "project1").mkdir(parents=True)
	return tmp_path

@pytest.mark.asyncio
async def test_spawn_gemini_single(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn --gemini project1 analyze this")
	
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["backend"] == "gemini"
	assert pending["project_path"] == str(spawn_dirs / "project1")
	assert "sender defaults to 'Gemini'" in pending["prompt"]
	assert pending["prompt"].endswith("analyze this")
	backend.send_spawn_ack.assert_called_once()

@pytest.mark.asyncio
async def test_spawn_claude_single(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn --claude project1 analyze this")
	
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["backend"] == "claude"
	assert "sender defaults to 'Claude'" in pending["prompt"]
	backend.send_spawn_ack.assert_called_once()

@pytest.mark.asyncio
async def test_spawn_heterogeneous_collab(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn --claude --gemini project1 pair up")
	
	pending = json.loads(_pending_path(cfg).read_text())
	assert "agents" in pending
	assert len(pending["agents"]) == 2
	assert pending["agents"][0]["backend"] == "claude"
	assert pending["agents"][0]["sender"] == "Claude"
	assert pending["agents"][1]["backend"] == "gemini"
	assert pending["agents"][1]["sender"] == "Gemini"
	assert "pair up" in pending["agents"][0]["prompt"]
	assert "pair up" in pending["agents"][1]["prompt"]
	backend.send_spawn_ack.assert_called_once()

@pytest.mark.asyncio
async def test_spawn_collab_legacy_flag(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn --collab project1 old school")
	
	pending = json.loads(_pending_path(cfg).read_text())
	assert "agents" in pending
	assert pending["agents"][0]["backend"] == "claude"
	assert pending["agents"][0]["sender"] == "Claude"
	assert pending["agents"][1]["backend"] == "claude"
	assert pending["agents"][1]["sender"] == "Claude"
	backend.send_spawn_ack.assert_called_once()

@pytest.mark.asyncio
async def test_spawn_default_is_claude(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn project1 just work")
	
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["backend"] == "claude"
	backend.send_spawn_ack.assert_called_once()
