"""Tests for SpawnHandler argument parsing, rate limiting, and task scheduler launch."""

from __future__ import annotations

import json
from datetime import timedelta, timezone, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger


def make_config(tmp_path: Path, spawn_root=None) -> Config:
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		spawn_root=spawn_root,
	)


def make_backend() -> MagicMock:
	backend = MagicMock()
	backend.send_text = AsyncMock()
	backend.send_spawn_ack = AsyncMock()
	return backend


@pytest.fixture
def spawn_dirs(tmp_path):
	(tmp_path / "rpdm" / "next-gen").mkdir(parents=True)
	return tmp_path


def _pending_path(cfg: Config) -> Path:
	return Path(cfg.log_path).parent / "spawn-pending.json"


# --- spawn not configured ---

@pytest.mark.asyncio
async def test_spawn_not_configured_sends_error(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path, spawn_root=None)
	backend = make_backend()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
	await handler.handle("/spawn rpdm/next-gen do stuff")
	backend.send_text.assert_called_once_with("Spawn not configured.")
	backend.send_spawn_ack.assert_not_called()


# --- four parsing forms (assert pending JSON content + schtasks call) ---

@pytest.mark.asyncio
async def test_form1_no_args_uses_spawn_root_and_default_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _DEFAULT_PROMPT
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run") as mock_run:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn")
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["prompt"] == _DEFAULT_PROMPT.format(project_key=spawn_dirs.name)
	assert pending["project_path"] == str(spawn_dirs)
	mock_run.assert_called_once()
	assert mock_run.call_args[0][0] == ["schtasks", "/run", "/tn", "SwitchboardSpawn"]
	backend.send_spawn_ack.assert_called_once_with(spawn_dirs.name, None)


@pytest.mark.asyncio
async def test_form2_subdir_no_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _DEFAULT_PROMPT
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn rpdm/next-gen")
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["prompt"] == _DEFAULT_PROMPT.format(project_key="rpdm/next-gen")
	assert pending["project_path"] == str(spawn_dirs / "rpdm" / "next-gen")
	backend.send_spawn_ack.assert_called_once_with("rpdm/next-gen", None)


@pytest.mark.asyncio
async def test_form3_no_path_with_prompt(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn fix the migration")
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["prompt"] == "fix the migration"
	assert pending["project_path"] == str(spawn_dirs)
	backend.send_spawn_ack.assert_called_once_with(spawn_dirs.name, "fix the migration")


@pytest.mark.asyncio
async def test_form4_subdir_with_prompt(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn rpdm/next-gen fix the migration")
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["prompt"] == "fix the migration"
	assert pending["project_path"] == str(spawn_dirs / "rpdm" / "next-gen")
	backend.send_spawn_ack.assert_called_once_with("rpdm/next-gen", "fix the migration")


# --- path traversal ---

@pytest.mark.asyncio
async def test_path_traversal_rejected(tmp_path):
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	outside = tmp_path / "outside"
	outside.mkdir()
	cfg = Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		spawn_root=spawn_root,
	)
	backend = make_backend()
	with patch("server.spawn.subprocess.run") as mock_run:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		# "../outside" resolves to tmp_path/outside which is outside spawn_root
		await handler.handle("/spawn ../outside do stuff")
	mock_run.assert_not_called()
	backend.send_text.assert_called_once()
	assert "Unknown project" in backend.send_text.call_args[0][0]


# --- rate limiting ---

@pytest.mark.asyncio
async def test_rate_limit_blocks_immediate_second_spawn(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn")
		backend.send_spawn_ack.reset_mock()
		await handler.handle("/spawn")
	backend.send_text.assert_called_once()
	assert "Rate limited" in backend.send_text.call_args[0][0]
	backend.send_spawn_ack.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_clears_after_60_seconds(spawn_dirs):
	from server.spawn import SpawnHandler, RATE_LIMIT_SECONDS
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn")
		handler._last_spawn_time = handler._last_spawn_time - timedelta(
			seconds=RATE_LIMIT_SECONDS + 1
		)
		backend.send_text.reset_mock()
		await handler.handle("/spawn")
	assert backend.send_spawn_ack.call_count == 2
	backend.send_text.assert_not_called()


# --- schtasks failure ---

@pytest.mark.asyncio
async def test_schtasks_failure_sends_error_and_cleans_pending(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run", side_effect=FileNotFoundError("schtasks not found")):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path))
		await handler.handle("/spawn")
	backend.send_text.assert_called_once()
	assert "Failed to spawn" in backend.send_text.call_args[0][0]
	backend.send_spawn_ack.assert_not_called()
	assert not _pending_path(cfg).exists()


# --- audit log ---

@pytest.mark.asyncio
async def test_spawn_started_logged_on_success(spawn_dirs):
	from server.spawn import SpawnHandler
	log_path = spawn_dirs / "log.jsonl"
	cfg = Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(log_path),
		spawn_root=spawn_dirs,
	)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(str(log_path)))
		await handler.handle("/spawn")
	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	spawn_events = [e for e in events if e["event"] == "spawn_started"]
	assert len(spawn_events) == 1
	assert spawn_events[0]["project_key"] == spawn_dirs.name
	assert spawn_events[0]["prompt_preview"] == "(ask on start)"
