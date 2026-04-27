"""Tests for the away_mode_commands Firebase listener dispatch (Slice K1)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry


def make_config(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


class FakeBackend:
	"""Minimal backend that supports away_mode_commands and bulk_respond_dialog."""

	def __init__(self, decision: dict | None = None):
		self._cmds: asyncio.Queue[dict] = asyncio.Queue()
		self._decision = decision
		self.dialog_writes: list[dict] = []
		self.dialog_cleared = False
		self.away_mirror_calls: list[tuple] = []
		self.resolution_confirmations: list[tuple] = []
		self.channel_writes: list[tuple] = []

	def push_command(self, cmd: dict):
		self._cmds.put_nowait(cmd)

	async def poll_away_mode_commands(self):
		while True:
			yield await self._cmds.get()

	async def poll_bulk_respond_decision(self) -> dict:
		if self._decision is None:
			raise NotImplementedError
		return self._decision

	async def write_bulk_respond_dialog(self, payload: dict) -> None:
		self.dialog_writes.append(payload)

	async def clear_bulk_respond_dialog(self) -> None:
		self.dialog_cleared = True

	async def write_away_mode_mirror(self, cwd, active) -> None:
		self.away_mirror_calls.append((cwd, active))

	async def fetch_message_text(self, cwd: str, msg_id: str) -> str | None:
		return None

	async def send_resolution_confirmation(self, request_id, channel_id, correlation, response_text=None):
		self.resolution_confirmations.append((request_id, channel_id, response_text))

	async def write_channel_message(
		self, cwd, sender, message_type, content,
		*, request_id=None, url=None, format="plain", suggestions=None, filename=None, title=None,
	):
		self.channel_writes.append((cwd, sender, message_type, content))
		return None, None


async def _run_one_cmd(registry: Registry, backend: FakeBackend, cmd: dict, tmp_path: Path):
	"""Push one command, run the dispatch loop until it processes it, then cancel."""
	from server.gateway import build_tool_handlers, dispatch_away_mode_commands
	cfg = make_config(tmp_path)
	logger = JsonlLogger(cfg.log_path)
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	backend.push_command(cmd)
	task = asyncio.create_task(dispatch_away_mode_commands(registry, backend, handlers, logger))
	# Give the loop time to process. Bulk-respond send_to_all fans out
	# send_resolution_confirmation + write_channel_message per pending plus
	# now-async logger calls per stage; the await chain is far longer than a
	# simple set_global_away. A 100ms wall-clock sleep is more reliable than
	# counting `sleep(0)` ticks because to_thread-backed logger writes return
	# from a worker thread, not a single event-loop yield.
	await asyncio.sleep(0.1)
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_enter_global_flips_global_away(tmp_path):
	registry = Registry()
	assert registry.global_away() is False
	backend = FakeBackend()
	await _run_one_cmd(registry, backend, {"type": "enter_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	assert registry.global_away() is True


@pytest.mark.asyncio
async def test_exit_global_no_pending_flips_global_away_false(tmp_path):
	registry = Registry()
	registry.set_global_away(True)
	backend = FakeBackend(decision={"action": "skip"})
	await _run_one_cmd(registry, backend, {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	assert registry.global_away() is False
	# No pending means write_bulk_respond_dialog should NOT be called
	assert backend.dialog_writes == []


@pytest.mark.asyncio
async def test_exit_global_send_to_all_resolves_pending_and_clears_global(tmp_path):
	registry = Registry()
	registry.set_global_away(True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1", msg_id="msg-1")
	backend = FakeBackend(decision={"action": "send_to_all", "default_text": "Back at desk"})
	await _run_one_cmd(registry, backend, {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	assert registry.global_away() is False
	assert fut.done()
	assert fut.result() == "Back at desk"
	assert backend.dialog_cleared is True


@pytest.mark.asyncio
async def test_exit_global_skip_leaves_pending_clears_global(tmp_path):
	registry = Registry()
	registry.set_global_away(True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")
	backend = FakeBackend(decision={"action": "skip"})
	await _run_one_cmd(registry, backend, {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	assert registry.global_away() is False
	assert not fut.done()
	assert backend.dialog_cleared is True


@pytest.mark.asyncio
async def test_exit_global_cancel_re_sets_global(tmp_path):
	registry = Registry()
	registry.set_global_away(True)
	registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")
	backend = FakeBackend(decision={"action": "cancel"})
	await _run_one_cmd(registry, backend, {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	assert registry.global_away() is True
	assert backend.dialog_cleared is True


@pytest.mark.asyncio
async def test_exit_global_send_to_all_honors_exit_when_fan_out_fails(tmp_path):
	"""Layer 1: a backend write failure inside _resolve_one must not abort the whole fan-out
	or leave global away stuck on. Pre-fix this raised AttributeError out of bulk_respond_send_to_all
	and the exit_global handler's outer except swallowed it without flipping the flag."""
	registry = Registry()
	registry.set_global_away(True)
	fut1 = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1", msg_id="msg-1")
	fut2 = registry.add(cwd="c:/work/bar", sender="Claude", request_id="req-2", msg_id="msg-2")

	class FanOutFailsBackend(FakeBackend):
		async def write_channel_message(self, cwd, *args, **kwargs):
			# First pending blows up; second should still complete cleanly.
			if cwd == "c:/work/foo":
				raise RuntimeError("simulated firebase blip")
			return await super().write_channel_message(cwd, *args, **kwargs)

	backend = FanOutFailsBackend(decision={"action": "send_to_all", "default_text": "Back at desk"})
	await _run_one_cmd(registry, backend, {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	# Both pendings resolved (registry.resolve runs before the writes)
	assert fut1.done() and fut1.result() == "Back at desk"
	assert fut2.done() and fut2.result() == "Back at desk"
	# Global flag honored exit despite the per-pending write failure
	assert registry.global_away() is False
	assert backend.dialog_cleared is True


@pytest.mark.asyncio
async def test_exit_global_honors_exit_when_dialog_write_fails(tmp_path):
	"""Layer 2: if the dialog flow itself can't run (e.g. Firebase write fails), the user's
	exit-global toggle must still flip the flag — they pressed exit, the system owes them exit."""
	registry = Registry()
	registry.set_global_away(True)
	registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")

	class DialogWriteFailsBackend(FakeBackend):
		async def write_bulk_respond_dialog(self, payload):
			raise RuntimeError("firebase down")

	backend = DialogWriteFailsBackend(decision={"action": "send_to_all"})
	await _run_one_cmd(registry, backend, {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	# Couldn't show the dialog — fall back to "skip" semantics (exit, no replies)
	assert registry.global_away() is False


@pytest.mark.asyncio
async def test_enter_cwd_sets_override(tmp_path):
	registry = Registry()
	backend = FakeBackend()
	await _run_one_cmd(
		registry, backend,
		{"type": "enter_cwd", "cwd": "c:/work/switchboard", "issued_at": "2026-01-01T00:00:00Z"},
		tmp_path,
	)
	assert registry.is_away_mode_active("c:/work/switchboard") is True


@pytest.mark.asyncio
async def test_exit_cwd_clears_override(tmp_path):
	registry = Registry()
	registry.set_cwd_override("c:/work/switchboard", True)
	backend = FakeBackend()
	await _run_one_cmd(
		registry, backend,
		{"type": "exit_cwd", "cwd": "c:/work/switchboard", "issued_at": "2026-01-01T00:00:00Z"},
		tmp_path,
	)
	assert registry.is_away_mode_active("c:/work/switchboard") is False


@pytest.mark.asyncio
async def test_enter_cwd_normalizes_git_bash_path(tmp_path):
	registry = Registry()
	backend = FakeBackend()
	await _run_one_cmd(
		registry, backend,
		{"type": "enter_cwd", "cwd": "/c/work/switchboard", "issued_at": "2026-01-01T00:00:00Z"},
		tmp_path,
	)
	assert registry.is_away_mode_active("c:/work/switchboard") is True
