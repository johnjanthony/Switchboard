"""Tests for SpawnHandler argument parsing, rate limiting, and task scheduler launch."""

from __future__ import annotations

import asyncio
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


def mock_subprocess_exec():
	"""Helper to mock asyncio.create_subprocess_exec."""
	mock_proc = AsyncMock()
	mock_proc.communicate.return_value = (b"ok", b"")
	mock_proc.returncode = 0
	return AsyncMock(return_value=mock_proc)


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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn")
	from server.canonicalization import canonicalize_cwd
	pending = json.loads(_pending_path(cfg).read_text())
	channel_id = pending["channel_id"]
	assert channel_id == canonicalize_cwd(str(spawn_dirs))

	expected = f"{_BASE_INSTRUCTION.format(sender_default='Claude')} {_DEFAULT_PROMPT}"
	assert pending["prompt"] == expected
	assert pending["project_path"] == str(spawn_dirs)
	mock_exec.assert_called_once()
	assert mock_exec.call_args[0] == ("schtasks", "/run", "/tn", "SwitchboardSpawn")
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_form2_subdir_no_prompt(spawn_dirs):
	from server.spawn import SpawnHandler, _DEFAULT_PROMPT, _BASE_INSTRUCTION
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		# "../outside" resolves to tmp_path/outside which is outside spawn_root
		await handler.handle("/spawn ../outside do stuff")
	mock_exec.assert_not_called()
	backend.send_text.assert_called_once()
	assert "Unknown project" in backend.send_text.call_args[0][0]


# --- rate limiting ---

@pytest.mark.asyncio
async def test_rate_limit_blocks_immediate_second_spawn(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
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
	with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("schtasks not found")):
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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
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
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away-mode.json")
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")
	expected_cwd = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	assert registry.is_away_mode_active(expected_cwd) is True
	assert registry.global_away() is False


@pytest.mark.asyncio
async def test_collab_spawn_auto_enters_away_mode(spawn_dirs):
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away-mode.json")
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen --collab review this")
	expected_cwd = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	assert registry.is_away_mode_active(expected_cwd) is True
	assert registry.global_away() is False


@pytest.mark.asyncio
async def test_single_spawn_does_not_set_away_mode_on_schtasks_failure(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	registry = Registry(away_mode_path=spawn_dirs / "away-mode.json")
	with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("schtasks boom")):
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
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn")
	# Sanity: /spawn still routes to the spawn path (pending file written).
	pending = _pending_path(cfg)
	assert pending.exists()


# --- cancel-on-spawn (in-flight ask_human cleanup) ---

@pytest.mark.asyncio
async def test_single_spawn_cancels_prior_pending_for_cwd(spawn_dirs):
	"""Cancel-on-spawn: stale pendings from a dead prior agent get marked cancelled in
	Firebase before the new agent launches. Closes test 6d's withdraw-on-respawn gap."""
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock()
	registry = Registry()

	target_cwd = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	fut = registry.add(cwd=target_cwd, sender="Claude", request_id="prior-req-1")

	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")

	backend.mark_question_cancelled.assert_called_once_with(target_cwd, "prior-req-1")
	assert fut.cancelled()
	assert registry.get((target_cwd, "Claude")) is None
	# spawn still ran successfully
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_single_spawn_does_not_affect_pendings_for_other_cwds(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock()
	registry = Registry()

	other_cwd = "c:/work/unrelated"
	fut_other = registry.add(cwd=other_cwd, sender="Claude", request_id="other-req-1")

	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")

	backend.mark_question_cancelled.assert_not_called()
	assert not fut_other.cancelled()


@pytest.mark.asyncio
async def test_single_spawn_no_prior_pending_skips_firebase_call(spawn_dirs):
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock()

	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	backend.mark_question_cancelled.assert_not_called()


@pytest.mark.asyncio
async def test_collab_spawn_cancels_all_prior_pending_for_cwd(spawn_dirs):
	"""Collab spawn: BYO collab can have multiple senders pending in the same channel — all of
	them must be cancelled when a fresh collab session lands on that cwd."""
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock()
	backend.start_inject_listener = AsyncMock()
	registry = Registry()

	target_cwd = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	registry.add(cwd=target_cwd, sender="Claude", request_id="prior-collab-1")
	registry.add(cwd=target_cwd, sender="Sparkles", request_id="prior-collab-2")

	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen --collab review this")

	assert backend.mark_question_cancelled.call_count == 2
	called_args = {call.args for call in backend.mark_question_cancelled.call_args_list}
	assert (target_cwd, "prior-collab-1") in called_args
	assert (target_cwd, "prior-collab-2") in called_args


@pytest.mark.asyncio
async def test_spawn_logs_pending_cancelled_event(spawn_dirs):
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock()
	registry = Registry()

	target_cwd = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	registry.add(cwd=target_cwd, sender="Claude", request_id="prior-1")

	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")

	events = [json.loads(line) for line in Path(cfg.log_path).read_text().splitlines() if line]
	cancelled_events = [e for e in events if e["event"] == "pending_cancelled_on_spawn"]
	assert len(cancelled_events) == 1
	assert cancelled_events[0]["cwd"] == target_cwd
	assert cancelled_events[0]["request_ids"] == ["prior-1"]
	assert cancelled_events[0]["count"] == 1


@pytest.mark.asyncio
async def test_spawn_continues_when_mark_cancelled_fails(spawn_dirs):
	"""If the Firebase mark_question_cancelled write fails, the spawn must still proceed
	(the prior pending is already cancelled in the registry — Firebase is a mirror)."""
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.mark_question_cancelled = AsyncMock(side_effect=RuntimeError("firebase blip"))
	registry = Registry()

	target_cwd = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	fut = registry.add(cwd=target_cwd, sender="Claude", request_id="prior-1")

	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), registry)
		await handler.handle("/spawn rpdm/next-gen do stuff")

	# Spawn proceeded despite Firebase failure
	backend.send_spawn_ack.assert_called_once()
	assert fut.cancelled()
	# Surface error logged
	events = [json.loads(line) for line in Path(cfg.log_path).read_text().splitlines() if line]
	surface_errors = [e for e in events if e["event"] == "surface_error"]
	assert any("mark_cancelled_failed_on_spawn" in e.get("detail", "") for e in surface_errors)


# --- nested project resolution ---

@pytest.mark.asyncio
async def test_nested_project_single_match_resolves(tmp_path):
	"""When tokens[0] doesn't exist at root but exists exactly once one level down,
	the spawn resolves to that nested path automatically (e.g. 'develop' → 'rpdm/develop')."""
	from server.spawn import SpawnHandler
	(tmp_path / "rpdm" / "develop").mkdir(parents=True)
	cfg = make_config(tmp_path, spawn_root=tmp_path)
	backend = make_backend()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn develop just say hi")
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["project_path"] == str(tmp_path / "rpdm" / "develop")
	# Prompt should NOT include the project token
	assert "just say hi" in pending["prompt"]
	assert "develop just say hi" not in pending["prompt"]


@pytest.mark.asyncio
async def test_nested_project_ambiguous_match_errors_with_suggestions(tmp_path):
	"""When tokens[0] matches multiple subdirs, spawn aborts with a suggestion list."""
	from server.spawn import SpawnHandler
	(tmp_path / "rpdm" / "develop").mkdir(parents=True)
	(tmp_path / "rpg-one" / "develop").mkdir(parents=True)
	(tmp_path / "rpdm_archive" / "develop").mkdir(parents=True)
	cfg = make_config(tmp_path, spawn_root=tmp_path)
	backend = make_backend()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn develop just say hi")
	mock_exec.assert_not_called()
	backend.send_text.assert_called_once()
	msg = backend.send_text.call_args[0][0]
	assert "Ambiguous project 'develop'" in msg
	assert "rpdm/develop" in msg
	assert "rpg-one/develop" in msg
	assert "rpdm_archive/develop" in msg


@pytest.mark.asyncio
async def test_no_match_preserves_terminal_form_fallback(spawn_dirs):
	"""'/spawn fix the migration' (where 'fix' is neither a top-level nor nested project)
	must still fall through and treat the entire input as the prompt — preserves the
	original terminal-form syntax."""
	from server.spawn import SpawnHandler, _BASE_INSTRUCTION
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn fix the migration")
	pending = json.loads(_pending_path(cfg).read_text())
	assert pending["project_path"] == str(spawn_dirs)
	assert "fix the migration" in pending["prompt"]


# --- /spawn collision integration (Item 3 — wires dialog into /spawn command path) ---

def make_spawn_with_collision_backend(decision: dict, meta: dict | None = None) -> MagicMock:
	"""Backend that simulates a colliding cwd and a phone-side decision response."""
	backend = make_backend()
	backend.has_messages = AsyncMock(return_value=True)
	backend.read_channel_meta = AsyncMock(return_value=meta or {
		"title": "Existing Channel",
		"last_activity_at": "2026-04-26T10:00:00+00:00",
		"hidden": False,
	})
	backend.write_spawn_collision_prompt = AsyncMock()
	backend.clear_spawn_collision_prompt = AsyncMock()
	backend.poll_spawn_collision_decision = AsyncMock(return_value=decision)
	backend.wipe_channel = AsyncMock()
	backend.set_channel_hidden = AsyncMock()
	backend.mark_question_cancelled = AsyncMock()
	return backend


@pytest.mark.asyncio
async def test_spawn_no_collision_proceeds_without_dialog(spawn_dirs):
	"""When the target cwd has no prior messages, /spawn skips the dialog flow entirely."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_backend()
	backend.has_messages = AsyncMock(return_value=False)
	backend.write_spawn_collision_prompt = AsyncMock()
	backend.poll_spawn_collision_decision = AsyncMock()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")
	backend.write_spawn_collision_prompt.assert_not_called()
	backend.poll_spawn_collision_decision.assert_not_called()
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_collision_cancel_aborts_launch(spawn_dirs):
	"""User picks Cancel at the collision dialog → no spawn happens, channel untouched."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "cancel"})
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	# Dialog flow ran
	backend.write_spawn_collision_prompt.assert_called_once()
	backend.poll_spawn_collision_decision.assert_called_once()
	backend.clear_spawn_collision_prompt.assert_called_once()
	# Spawn was aborted
	mock_exec.assert_not_called()
	backend.send_spawn_ack.assert_not_called()
	backend.wipe_channel.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_collision_continue_launches_without_wiping(spawn_dirs):
	"""User picks Continue → spawn proceeds, channel keeps its existing history."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "continue"})
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	backend.write_spawn_collision_prompt.assert_called_once()
	backend.poll_spawn_collision_decision.assert_called_once()
	backend.clear_spawn_collision_prompt.assert_called_once()
	backend.wipe_channel.assert_not_called()
	# Spawn proceeded
	mock_exec.assert_called_once()
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_collision_clear_wipes_and_launches(spawn_dirs):
	"""User picks Clear → channel is wiped (incl. hidden flag reset) and spawn proceeds."""
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(
		decision={"action": "clear"},
		meta={"title": "Old", "last_activity_at": "2026-04-25T10:00Z", "hidden": True},
	)
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	canonical = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	backend.wipe_channel.assert_called_once_with(canonical)
	backend.set_channel_hidden.assert_called_once_with(canonical, False)
	mock_exec.assert_called_once()
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_collision_dialog_logged(spawn_dirs):
	"""The collision-detection event is audit-logged."""
	from server.spawn import SpawnHandler
	from server.canonicalization import canonicalize_cwd
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "cancel"})
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	canonical = canonicalize_cwd(str(spawn_dirs / "rpdm" / "next-gen"))
	events = [json.loads(line) for line in Path(cfg.log_path).read_text().splitlines() if line]
	collisions = [e for e in events if e["event"] == "spawn_collision_detected"]
	assert len(collisions) == 1
	assert collisions[0]["cwd"] == canonical


@pytest.mark.asyncio
async def test_spawn_collision_poll_not_implemented_falls_through(spawn_dirs):
	"""Backend without listener support (NotImplementedError) → spawn proceeds best-effort."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "cancel"})
	backend.poll_spawn_collision_decision = AsyncMock(side_effect=NotImplementedError)
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	# Dialog cleared even though decision couldn't be polled
	backend.clear_spawn_collision_prompt.assert_called_once()
	# Spawn still proceeded
	mock_exec.assert_called_once()
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_collision_dialog_write_fail_falls_through(spawn_dirs):
	"""If the dialog can't even be written, spawn proceeds rather than blocking the user."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "cancel"})
	backend.write_spawn_collision_prompt = AsyncMock(side_effect=RuntimeError("firebase down"))
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")

	# Spawn still happens
	mock_exec.assert_called_once()
	backend.send_spawn_ack.assert_called_once()


@pytest.mark.asyncio
async def test_spawn_collision_cancel_does_not_bump_rate_limit(spawn_dirs):
	"""Cancelling at the dialog must not consume the user's rate-limit slot — they should be
	able to immediately try a different /spawn command without waiting 60s."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "cancel"})
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec):
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen do stuff")
	# Rate limiter is set inside _handle_single_spawn, which never ran on cancel
	assert handler._last_spawn_time is None


@pytest.mark.asyncio
async def test_spawn_collision_continue_with_collab(spawn_dirs):
	"""Collision flow also covers --collab: continue should fan out the collab launch."""
	from server.spawn import SpawnHandler
	cfg = make_config(spawn_dirs, spawn_root=spawn_dirs)
	backend = make_spawn_with_collision_backend(decision={"action": "continue"})
	backend.start_inject_listener = AsyncMock()
	with patch("asyncio.create_subprocess_exec", new_callable=mock_subprocess_exec) as mock_exec:
		handler = SpawnHandler(cfg, backend, JsonlLogger(cfg.log_path), Registry())
		await handler.handle("/spawn rpdm/next-gen --collab review this")
	mock_exec.assert_called_once()
	backend.send_spawn_ack.assert_called_once()
