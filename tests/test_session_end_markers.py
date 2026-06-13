"""T-146: reliable SessionEnd dormancy via marker files + server sweep.

Claude Code SessionEnd hooks are fire-and-forget and do not block process exit,
so the prior synchronous HTTP POST raced termination and was dropped. The hook
now writes a marker file (a fast filesystem write that wins the race); the
server sweeps markers and applies handle_session_end."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "scripts" / "cli-session-end-hook.py"


def test_hook_writes_marker_file(tmp_path):
	"""The hook reads {session_id, reason} from stdin and writes
	<SWITCHBOARD_MARKER_DIR>/<session_id>.json with session_id, reason, ended_at."""
	env = {**os.environ, "SWITCHBOARD_MARKER_DIR": str(tmp_path)}
	payload = json.dumps({"session_id": "sess-abc-123", "reason": "other"}).encode("utf-8")
	subprocess.run([sys.executable, str(_HOOK)], input=payload, env=env, check=True, timeout=10)

	marker = tmp_path / "sess-abc-123.json"
	assert marker.exists(), "hook must write a marker file named <session_id>.json"
	data = json.loads(marker.read_text(encoding="utf-8"))
	assert data["session_id"] == "sess-abc-123"
	assert data["reason"] == "other"
	assert isinstance(data.get("ended_at"), str) and data["ended_at"]


def test_hook_writes_nothing_without_session_id(tmp_path):
	"""No session_id in the payload -> no marker file (and no crash)."""
	env = {**os.environ, "SWITCHBOARD_MARKER_DIR": str(tmp_path)}
	payload = json.dumps({"reason": "other"}).encode("utf-8")
	subprocess.run([sys.executable, str(_HOOK)], input=payload, env=env, check=True, timeout=10)
	assert list(tmp_path.glob("*.json")) == []


import pytest as _pytest
from datetime import datetime, timezone


def _make_logger(tmp_path):
	from server.logging_jsonl import JsonlLogger
	return JsonlLogger(str(tmp_path / "log.jsonl"))


@_pytest.mark.asyncio
async def test_sweep_marks_member_dormant_and_deletes_marker(tmp_path):
	"""A marker for a bound member: the sweep marks the member dormant, uses the
	marker's ended_at for session_ended_at, and deletes the marker."""
	from server.gateway.dispatch import _sweep_session_end_markers
	from server.registry import Registry
	from tests.conftest import make_active_conversation

	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	marker_dir = tmp_path / "session-end"
	marker_dir.mkdir()
	(marker_dir / "s-1.json").write_text(json.dumps({
		"session_id": "s-1", "reason": "other", "ended_at": "2026-06-12T00:00:00+00:00",
	}), encoding="utf-8")

	await _sweep_session_end_markers(registry, marker_dir, backend=None, logger=_make_logger(tmp_path))

	member = conv.members_active["Claude"]
	assert member.alive is False
	assert member.session_end_reason == "other"
	assert member.session_lost_permanently is False
	assert member.session_ended_at == "2026-06-12T00:00:00+00:00"  # marker's ended_at, not sweep time
	assert "s-1" not in registry.session_to_conversation_id  # binding cleared
	assert list(marker_dir.glob("*.json")) == []  # marker deleted


@_pytest.mark.asyncio
async def test_sweep_idempotent_for_unbound_session(tmp_path):
	"""A marker for a session that is not bound (already handled, or never a
	member) is harmless: no crash, marker still deleted."""
	from server.gateway.dispatch import _sweep_session_end_markers
	from server.registry import Registry

	registry = Registry()
	marker_dir = tmp_path / "session-end"
	marker_dir.mkdir()
	(marker_dir / "s-gone.json").write_text(json.dumps({
		"session_id": "s-gone", "reason": "other", "ended_at": "2026-06-12T00:00:00+00:00",
	}), encoding="utf-8")

	processed = await _sweep_session_end_markers(registry, marker_dir, backend=None, logger=_make_logger(tmp_path))

	assert processed == 1
	assert list(marker_dir.glob("*.json")) == []  # deleted even though no member existed


@_pytest.mark.asyncio
async def test_sweep_deletes_malformed_marker(tmp_path):
	"""A malformed marker (bad JSON) is logged and deleted, not left to wedge the sweep."""
	from server.gateway.dispatch import _sweep_session_end_markers
	from server.registry import Registry

	registry = Registry()
	marker_dir = tmp_path / "session-end"
	marker_dir.mkdir()
	(marker_dir / "bad.json").write_text("{not json", encoding="utf-8")

	# Capture surface_error calls so we can assert the error was actually logged.
	errors: list[str] = []

	class _CapturingLogger:
		async def surface_error(self, msg: str, **_kw) -> None:
			errors.append(msg)

	await _sweep_session_end_markers(registry, marker_dir, backend=None, logger=_CapturingLogger())

	assert list(marker_dir.glob("*.json")) == []
	assert any("session_end_marker_failed" in e for e in errors), (
		f"expected session_end_marker_failed in logged errors; got: {errors}"
	)


@_pytest.mark.asyncio
async def test_sweep_missing_dir_is_noop(tmp_path):
	"""No marker dir yet -> sweep returns 0 without error."""
	from server.gateway.dispatch import _sweep_session_end_markers
	from server.registry import Registry
	processed = await _sweep_session_end_markers(registry=Registry(), marker_dir=tmp_path / "nope", backend=None, logger=_make_logger(tmp_path))
	assert processed == 0


@_pytest.mark.asyncio
async def test_dispatch_loop_processes_then_can_be_cancelled(tmp_path):
	"""The loop sweeps on its first tick (marking the member dormant) and exits
	cleanly on cancellation."""
	import asyncio
	from server.gateway.dispatch import dispatch_session_end_markers
	from server.registry import Registry
	from tests.conftest import make_active_conversation, _make_loop_supervisor

	registry = Registry()
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s-1", sender="Claude")
	registry.conversations["conv-1"] = conv
	registry.bind_session("s-1", "conv-1")

	marker_dir = tmp_path / "session-end"
	marker_dir.mkdir()
	(marker_dir / "s-1.json").write_text(json.dumps({
		"session_id": "s-1", "reason": "other", "ended_at": "2026-06-12T00:00:00+00:00",
	}), encoding="utf-8")

	logger = _make_logger(tmp_path)
	supervisor = _make_loop_supervisor(None, logger, "dispatch_session_end_markers")
	task = asyncio.create_task(
		dispatch_session_end_markers(registry, None, logger, supervisor, marker_dir, interval=0.05)
	)
	# Let the first tick run.
	for _ in range(5):
		await asyncio.sleep(0)
	await asyncio.sleep(0.1)

	assert conv.members_active["Claude"].alive is False
	assert list(marker_dir.glob("*.json")) == []

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
