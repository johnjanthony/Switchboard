"""Tests for SpawnHandler argument parsing, rate limiting, and task scheduler launch."""

from __future__ import annotations

import json
import re as _re
from datetime import timedelta, timezone, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry


def make_config(tmp_path: Path, spawn_root=None) -> Config:
	return Config(
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
	backend.write_session_meta = AsyncMock()
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
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	await handler.handle("/spawn rpdm/next-gen do stuff")
	backend.send_text.assert_called_once_with("Spawn not configured.")
	backend.send_spawn_ack.assert_not_called()


# --- four parsing forms (assert pending JSON content + schtasks call) ---

@pytest.mark.asyncio
async def test_form1_no_args_uses_spawn_root_and_default_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _DEFAULT_PROMPT, _BASE_INSTRUCTION
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run") as mock_run:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn")
	pending = json.loads(_pending_path(cfg).read_text())
	channel_id = pending["channel_id"]
	assert _re.match(r".+-\d{8}-\d{6}$", channel_id)

	expected = f"{_BASE_INSTRUCTION.format(sender_default='Claude')} {_DEFAULT_PROMPT}"
	assert pending["prompt"] == expected
	assert pending["project_path"] == str(spawn_dirs)
	mock_run.assert_called_once()
	assert mock_run.call_args[0][0] == ["schtasks", "/run", "/tn", "SwitchboardSpawn"]
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_form2_subdir_no_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _DEFAULT_PROMPT, _BASE_INSTRUCTION
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen")
	pending = json.loads(_pending_path(cfg).read_text())
	expected = f"{_BASE_INSTRUCTION.format(sender_default='Claude')} {_DEFAULT_PROMPT}"
	assert pending["prompt"] == expected
	assert pending["project_path"] == str(spawn_dirs / "rpdm" / "next-gen")
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_form3_no_path_with_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _BASE_INSTRUCTION
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn fix the migration")
	pending = json.loads(_pending_path(cfg).read_text())
	expected = f"{_BASE_INSTRUCTION.format(sender_default='Claude')} fix the migration"
	assert pending["prompt"] == expected
	assert pending["project_path"] == str(spawn_dirs)
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_form4_subdir_with_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _BASE_INSTRUCTION
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen fix the migration")
	pending = json.loads(_pending_path(cfg).read_text())
	expected = f"{_BASE_INSTRUCTION.format(sender_default='Claude')} fix the migration"
	assert pending["prompt"] == expected
	assert pending["project_path"] == str(spawn_dirs / "rpdm" / "next-gen")
	backend.send_spawn_ack.assert_called_once()


# --- path traversal ---

@pytest.mark.asyncio
async def test_path_traversal_rejected(tmp_path):
	from server.spawn import SpawnHandler
	spawn_root = tmp_path / "projects"
	spawn_root.mkdir()
	outside = tmp_path / "outside"
	outside.mkdir()
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		spawn_root=spawn_root,
	)
	backend = make_backend()
	with patch("server.spawn.subprocess.run") as mock_run:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
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
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
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
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
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
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
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
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(log_path),
		spawn_root=spawn_dirs,
	)
	backend = make_backend()
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(str(log_path)), Registry())
		await handler.handle("/spawn")
	events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
	spawn_events = [e for e in events if e["event"] == "spawn_started"]
	assert len(spawn_events) == 1
	assert spawn_events[0]["project_key"] == spawn_dirs.name
	assert spawn_events[0]["prompt_preview"] == "(ask on start)"


@pytest.mark.asyncio
async def test_single_spawn_auto_enters_away_mode(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away-mode.json")
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")
	assert registry.global_away() is True


@pytest.mark.asyncio
async def test_collab_spawn_auto_enters_away_mode(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away-mode.json")
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen --collab review this")
	assert registry.global_away() is True


@pytest.mark.asyncio
async def test_single_spawn_does_not_set_away_mode_on_schtasks_failure(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away-mode.json")
	with patch("server.spawn.subprocess.run", side_effect=RuntimeError("schtasks boom")):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")
	assert registry.global_away() is False


# --- /away-mode command dispatch ---

def _read_events(cfg: Config) -> list[dict]:
	log = Path(cfg.log_path)
	if not log.exists():
		return []
	return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_away_mode_on_command_sets_flag_and_audits(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path, spawn_root=tmp_path)
	backend = make_backend()
	registry = Registry(away_mode_path=tmp_path / "away.json")
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	assert registry.global_away() is False

	await handler.handle("/away-mode on")

	assert registry.global_away() is True
	events = _read_events(cfg)
	entered = [e for e in events if e.get("event") == "away_mode_entered"]
	assert entered and entered[-1].get("reason") == "android"


@pytest.mark.asyncio
async def test_away_mode_off_command_clears_flag_and_audits(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path, spawn_root=tmp_path)
	backend = make_backend()
	registry = Registry(away_mode_path=tmp_path / "away.json")
	registry.set_global_away(True)
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	await handler.handle("/away-mode off")

	assert registry.global_away() is False
	events = _read_events(cfg)
	exited = [e for e in events if e.get("event") == "away_mode_exited"]
	assert exited and exited[-1].get("reason") == "android"


@pytest.mark.asyncio
async def test_away_mode_unknown_subcommand_is_ignored(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path, spawn_root=tmp_path)
	backend = make_backend()
	registry = Registry(away_mode_path=tmp_path / "away.json")
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)

	await handler.handle("/away-mode wobble")

	assert registry.global_away() is False
	events = _read_events(cfg)
	# Must not have emitted entered/exited audit events
	assert not any(
		e.get("event") in ("away_mode_entered", "away_mode_exited")
		for e in events
	)
	# Positive assertion: the unknown-subcommand path MUST call surface_error
	# with a descriptive detail so future refactors can't silently swallow it.
	surface_errors = [e for e in events if e.get("event") == "surface_error"]
	assert len(surface_errors) == 1
	assert "away_mode_unknown_subcommand" in surface_errors[0].get("detail", "")
	assert "wobble" in surface_errors[0].get("detail", "")


@pytest.mark.asyncio
async def test_spawn_command_still_works(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away.json")
	with patch("server.spawn.subprocess.run"):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn")
	# Sanity: /spawn still routes to the spawn path (pending file written).
	pending = _pending_path(cfg)
	assert pending.exists()


# --- submit / resolve_collision ---

def make_collision_backend(has_messages_result: bool = False, meta: dict | None = None) -> MagicMock:
	backend = make_backend()
	backend.has_messages = AsyncMock(return_value=has_messages_result)
	backend.read_channel_meta = AsyncMock(return_value=meta or {
		"title": "My Channel",
		"last_activity_at": "2026-04-24T10:00:00+00:00",
		"hidden": False,
	})
	backend.write_spawn_collision_prompt = AsyncMock()
	backend.clear_spawn_collision_prompt = AsyncMock()
	backend.wipe_channel = AsyncMock()
	backend.set_channel_hidden = AsyncMock()
	return backend


@pytest.mark.asyncio
async def test_submit_no_collision_proceeds_silently(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend(has_messages_result=False)
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	result = await handler.submit("c:/work/foo", ["claude"])
	assert "collision" not in result
	backend.write_spawn_collision_prompt.assert_not_called()
	backend.clear_spawn_collision_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_submit_collision_returns_dialog_data(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend(has_messages_result=True, meta={
		"title": "RPDM Review",
		"last_activity_at": "2026-04-24T09:00:00+00:00",
		"hidden": False,
	})
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	result = await handler.submit("c:/work/rpdm", ["claude"])
	assert result["collision"] is True
	assert "spawn_id" in result
	assert result["channel_title"] == "RPDM Review"
	assert result["last_activity_at"] == "2026-04-24T09:00:00+00:00"
	assert result["hidden"] is False
	backend.write_spawn_collision_prompt.assert_called_once()
	# pending entry stored
	spawn_id = result["spawn_id"]
	assert spawn_id in handler._pending_collisions


@pytest.mark.asyncio
async def test_submit_collision_logs_event(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend(has_messages_result=True)
	logger = JsonlLogger(cfg.log_path)
	handler = SpawnHandler(cfg, backend, logger, Registry())
	result = await handler.submit("c:/work/rpdm", ["claude"])
	events = [json.loads(line) for line in Path(cfg.log_path).read_text().splitlines() if line]
	collision_events = [e for e in events if e["event"] == "spawn_collision_detected"]
	assert len(collision_events) == 1
	assert collision_events[0]["spawn_id"] == result["spawn_id"]


@pytest.mark.asyncio
async def test_resolve_collision_continue_launches_without_wiping(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend(has_messages_result=True)
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	collision = await handler.submit("c:/work/rpdm", ["claude"])
	spawn_id = collision["spawn_id"]
	result = await handler.resolve_collision(spawn_id, "continue")
	assert "launched" in result
	backend.wipe_channel.assert_not_called()
	backend.clear_spawn_collision_prompt.assert_called_once()
	assert spawn_id not in handler._pending_collisions


@pytest.mark.asyncio
async def test_resolve_collision_clear_wipes_and_launches(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend(has_messages_result=True)
	registry = Registry()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
	collision = await handler.submit("c:/work/rpdm", ["claude"])
	spawn_id = collision["spawn_id"]
	result = await handler.resolve_collision(spawn_id, "clear")
	assert "launched" in result
	backend.wipe_channel.assert_called_once_with("c:/work/rpdm")
	backend.set_channel_hidden.assert_called_once_with("c:/work/rpdm", False)
	assert registry.is_away_mode_active("c:/work/rpdm") is True
	backend.clear_spawn_collision_prompt.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_collision_cancel_aborts(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend(has_messages_result=True)
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	collision = await handler.submit("c:/work/rpdm", ["claude"])
	spawn_id = collision["spawn_id"]
	result = await handler.resolve_collision(spawn_id, "cancel")
	assert result == {"cancelled": True}
	backend.wipe_channel.assert_not_called()
	backend.clear_spawn_collision_prompt.assert_called_once_with(spawn_id)
	assert spawn_id not in handler._pending_collisions


@pytest.mark.asyncio
async def test_resolve_collision_unknown_spawn_id_returns_error(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	result = await handler.resolve_collision("nonexistent-id", "continue")
	assert "error" in result
	assert "unknown spawn_id" in result["error"]


@pytest.mark.asyncio
async def test_submit_invalid_cwd_returns_error(tmp_path):
	from server.spawn import SpawnHandler
	cfg = make_config(tmp_path)
	backend = make_collision_backend()
	handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
	result = await handler.submit("not-absolute", ["claude"])
	assert "error" in result
