"""Tests for the away_mode_commands Firebase listener dispatch.

The queue handler is decision-on-command: the phone shows the bulk-respond
dialog locally and sends the user's choice as `decision` + `default_text`
fields on the exit command. The server applies the decision and only flips
the away-mode mirror if the decision allows it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_registry_with_loopback as _make_registry_with_loopback, _make_loop_supervisor


def make_config(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


class FakeBackend:
	"""Minimal backend that supports away_mode_commands.

	Decisions ride on the queue command (Task 9 → client-driven), so this fake
	only records the surface the queue handler actually exercises:
	away-mode mirror writes, resolution confirmations, and channel writes."""

	def __init__(self):
		self._cmds: asyncio.Queue[dict] = asyncio.Queue()
		self.away_mirror_calls: list[tuple] = []
		self.resolution_confirmations: list[tuple] = []
		self.channel_writes: list[tuple] = []

	def push_command(self, cmd: dict):
		self._cmds.put_nowait(cmd)

	async def poll_away_mode_commands(self):
		while True:
			yield await self._cmds.get()

	async def write_away_mode_mirror(self, cwd, active) -> None:
		self.away_mirror_calls.append((cwd, active))

	async def send_resolution_confirmation(self, request_id, channel_id, correlation, response_text=None):
		self.resolution_confirmations.append((request_id, channel_id, response_text))

	async def write_channel_message(
		self, cwd, sender, message_type, content, **kwargs,
	):
		self.channel_writes.append((cwd, sender, message_type, content, kwargs))
		return None, None


async def _run_one_cmd(registry: Registry, backend: FakeBackend, cmd: dict, tmp_path: Path):
	"""Push one command, run the dispatch loop until it processes it, then cancel."""
	from server.gateway import dispatch_away_mode_commands
	cfg = make_config(tmp_path)
	logger = JsonlLogger(cfg.log_path)
	backend.push_command(cmd)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_away_mode_commands")
	task = asyncio.create_task(dispatch_away_mode_commands(registry, backend, logger, sup))
	# Give the loop time to process. send_default fans out
	# send_resolution_confirmation + write_channel_message per pending plus
	# now-async logger calls per stage; the await chain is far longer than a
	# simple state flip. A 100ms wall-clock sleep is more reliable than
	# counting `sleep(0)` ticks because to_thread-backed logger writes return
	# from a worker thread, not a single event-loop yield.
	await asyncio.sleep(0.1)
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


# ---------------------------------------------------------------------------
# enter_global
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_global_writes_mirror_true(tmp_path):
	registry = _make_registry_with_loopback()
	backend = FakeBackend()
	await _run_one_cmd(registry, backend, {"type": "enter_global", "issued_at": "2026-01-01T00:00:00Z"}, tmp_path)
	# Queue handler bypasses registry.set_* and writes the mirror directly;
	# the listener (Task 8) fires update_global_away_cache once Firebase confirms.
	assert (None, True) in backend.away_mirror_calls


# ---------------------------------------------------------------------------
# exit_global
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_global_send_default_via_command(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_global_away_cache(True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1", msg_id="msg-1")

	backend = FakeBackend()
	cmd = {
		"type": "exit_global",
		"decision": "send_default",
		"default_text": "Back at desk",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	assert fut.done() and fut.result() == "Back at desk"
	# write_away_mode_mirror called with (None, False) to flip global off
	assert (None, False) in backend.away_mirror_calls
	# write_channel_message must pass attached_to_msg_id linking the reply to the question's msg_id
	human_msgs = [m for m in backend.channel_writes if m[2] == "human"]
	assert len(human_msgs) >= 1
	_, _, _, _, kwargs = human_msgs[0]
	assert kwargs.get("attached_to_msg_id") == "msg-1", (
		"send_default must pass attached_to_msg_id linking back to the question's msg_id"
	)


@pytest.mark.asyncio
async def test_exit_global_skip_via_command(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_global_away_cache(True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")

	backend = FakeBackend()
	cmd = {
		"type": "exit_global",
		"decision": "skip",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	assert not fut.done()
	assert (None, False) in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_global_cancel_via_command(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_global_away_cache(True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")

	backend = FakeBackend()
	cmd = {
		"type": "exit_global",
		"decision": "cancel",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	assert not fut.done()
	# State NOT flipped on cancel
	assert (None, False) not in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_global_no_decision_no_pending_just_flips(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_global_away_cache(True)
	# No pending; phone should send the command without a decision
	backend = FakeBackend()
	cmd = {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}
	await _run_one_cmd(registry, backend, cmd, tmp_path)
	assert (None, False) in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_global_no_decision_with_pending_treats_as_cancel(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_global_away_cache(True)
	registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")
	backend = FakeBackend()
	cmd = {"type": "exit_global", "issued_at": "2026-01-01T00:00:00Z"}
	await _run_one_cmd(registry, backend, cmd, tmp_path)
	assert (None, False) not in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_global_send_default_honors_exit_when_fan_out_fails(tmp_path):
	"""A backend write failure inside the per-pending fan-out must not abort the
	whole flow or strand the user in away mode after they pressed exit."""
	registry = _make_registry_with_loopback()
	registry.update_global_away_cache(True)
	fut1 = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1", msg_id="msg-1")
	fut2 = registry.add(cwd="c:/work/bar", sender="Claude", request_id="req-2", msg_id="msg-2")

	class FanOutFailsBackend(FakeBackend):
		async def write_channel_message(self, cwd, *args, **kwargs):
			# First pending blows up; second should still complete cleanly.
			if cwd == "c:/work/foo":
				raise RuntimeError("simulated firebase blip")
			return await super().write_channel_message(cwd, *args, **kwargs)

	backend = FanOutFailsBackend()
	cmd = {
		"type": "exit_global",
		"decision": "send_default",
		"default_text": "Back at desk",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	# Both pendings resolved (registry.resolve runs before the writes)
	assert fut1.done() and fut1.result() == "Back at desk"
	assert fut2.done() and fut2.result() == "Back at desk"
	# Global flag honored exit despite the per-pending write failure
	assert (None, False) in backend.away_mirror_calls


# ---------------------------------------------------------------------------
# enter_cwd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_cwd_writes_mirror_true(tmp_path):
	registry = _make_registry_with_loopback()
	backend = FakeBackend()
	await _run_one_cmd(
		registry, backend,
		{"type": "enter_cwd", "cwd": "c:/work/switchboard", "issued_at": "2026-01-01T00:00:00Z"},
		tmp_path,
	)
	assert ("c:/work/switchboard", True) in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_enter_cwd_normalizes_git_bash_path(tmp_path):
	registry = _make_registry_with_loopback()
	backend = FakeBackend()
	await _run_one_cmd(
		registry, backend,
		{"type": "enter_cwd", "cwd": "/c/work/switchboard", "issued_at": "2026-01-01T00:00:00Z"},
		tmp_path,
	)
	assert ("c:/work/switchboard", True) in backend.away_mirror_calls


# ---------------------------------------------------------------------------
# exit_cwd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_cwd_send_default_via_command(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_cwd_override_cache("c:/work/foo", True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1", msg_id="msg-1")
	# Another channel's pending should NOT be resolved
	registry.update_cwd_override_cache("c:/work/bar", True)
	fut_other = registry.add(cwd="c:/work/bar", sender="Claude", request_id="req-2", msg_id="msg-2")

	backend = FakeBackend()
	cmd = {
		"type": "exit_cwd",
		"cwd": "c:/work/foo",
		"decision": "send_default",
		"default_text": "Back at desk",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	assert fut.done() and fut.result() == "Back at desk"
	# Other channel unaffected
	assert not fut_other.done()
	assert ("c:/work/foo", False) in backend.away_mirror_calls
	# We did NOT touch the other channel's mirror
	assert ("c:/work/bar", False) not in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_cwd_skip_via_command(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_cwd_override_cache("c:/work/foo", True)
	fut = registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")

	backend = FakeBackend()
	cmd = {
		"type": "exit_cwd",
		"cwd": "c:/work/foo",
		"decision": "skip",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	assert not fut.done()
	assert ("c:/work/foo", False) in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_cwd_cancel_via_command(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_cwd_override_cache("c:/work/foo", True)
	registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")

	backend = FakeBackend()
	cmd = {
		"type": "exit_cwd",
		"cwd": "c:/work/foo",
		"decision": "cancel",
		"issued_at": "2026-01-01T00:00:00Z",
	}
	await _run_one_cmd(registry, backend, cmd, tmp_path)

	assert ("c:/work/foo", False) not in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_cwd_no_decision_no_pending_just_flips(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_cwd_override_cache("c:/work/foo", True)
	backend = FakeBackend()
	cmd = {"type": "exit_cwd", "cwd": "c:/work/foo", "issued_at": "2026-01-01T00:00:00Z"}
	await _run_one_cmd(registry, backend, cmd, tmp_path)
	assert ("c:/work/foo", False) in backend.away_mirror_calls


@pytest.mark.asyncio
async def test_exit_cwd_no_decision_with_pending_treats_as_cancel(tmp_path):
	registry = _make_registry_with_loopback()
	registry.update_cwd_override_cache("c:/work/foo", True)
	registry.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")
	backend = FakeBackend()
	cmd = {"type": "exit_cwd", "cwd": "c:/work/foo", "issued_at": "2026-01-01T00:00:00Z"}
	await _run_one_cmd(registry, backend, cmd, tmp_path)
	assert ("c:/work/foo", False) not in backend.away_mirror_calls
