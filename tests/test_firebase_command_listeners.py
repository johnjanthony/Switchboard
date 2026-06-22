"""Tests for the shared command-queue listener (combine/force-end/spawn)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend(monkeypatch, loop):
	"""Build a FirebaseBackend via __new__ so __init__ (which calls firebase_admin)
	is never invoked.  Only the attributes touched by the two listener methods are
	populated."""
	from server import firebase as fb_module

	# Patch db so SupervisedListener's db.reference() call is intercepted.
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)

	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._supervised = {}
	be._logger = None
	be._loop = loop
	return be


class _FakeSupervised:
	"""Stand-in for SupervisedListener — records start() calls and exposes the
	callback so tests can fire synthetic events."""

	instances: list["_FakeSupervised"] = []

	def __init__(self, *, name, path, callback, error_logger, loop, **_kw):
		self.name = name
		self.path = path
		self.callback = callback
		self.started = False
		_FakeSupervised.instances.append(self)

	def start(self):
		self.started = True


class _Event:
	"""Minimal stand-in for a Firebase SSE event."""
	def __init__(self, event_type: str, path: str, data):
		self.event_type = event_type
		self.path = path
		self.data = data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_combine_command_listener_invokes_handler_on_new_entry(monkeypatch):
	"""Handler is called when a new /combine_commands child arrives."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	# Patch SupervisedListener so we can intercept the callback.
	import server.firebase as fb_module
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(
		fb_module.firebase_supervisor if hasattr(fb_module, "firebase_supervisor") else __import__("server.firebase_supervisor", fromlist=["SupervisedListener"]),
		"SupervisedListener",
		_FakeSupervised,
	)
	# The import inside the method uses "from server.firebase_supervisor import SupervisedListener"
	# so we patch it in that module directly.
	import server.firebase_supervisor as sup_mod
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	received: list[dict] = []

	async def _handler(cmd, ack=None):
		received.append(cmd)

	await be.start_combine_command_listener(_handler)

	assert len(_FakeSupervised.instances) == 1
	sup = _FakeSupervised.instances[0]
	assert sup.path == "combine_commands"
	assert sup.started

	# Fire the initial bulk-load event (path == "/"): queued commands written
	# while the server was down MUST be dispatched on (re)connect (H12/M13).
	from datetime import datetime, timezone
	queued = {
		"source_conversation_id": "a",
		"target_conversation_id": "b",
		"issued_at": datetime.now(timezone.utc).isoformat(),
	}
	sup.callback(_Event("put", "/", {"old_key": queued}))
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert received == [queued]

	# Fire a new-child event (path == "/<push_id>").
	cmd = {"source_conversation_id": "conv-1", "target_conversation_id": "conv-2", "issued_at": datetime.now(timezone.utc).isoformat()}
	sup.callback(_Event("put", "/-Nxyz123", cmd))
	# Two yields: first lets call_soon_threadsafe flush; second runs the created task.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert cmd in received


@pytest.mark.asyncio
async def test_force_end_command_listener_invokes_handler_on_new_entry(monkeypatch):
	"""Handler is called when a new /force_end_commands child arrives."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	received: list[dict] = []

	async def _handler(cmd, ack=None):
		received.append(cmd)

	await be.start_force_end_command_listener(_handler)

	assert len(_FakeSupervised.instances) == 1
	sup = _FakeSupervised.instances[0]
	assert sup.path == "force_end_commands"
	assert sup.started

	# Fire the initial bulk-load event: queued commands must dispatch (H12/M13).
	from datetime import datetime, timezone
	queued = {"conversation_id": "conv-old", "issued_at": datetime.now(timezone.utc).isoformat()}
	sup.callback(_Event("put", "/", {"old_key": queued}))
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert received == [queued]

	# Fire a new-child event.
	cmd = {"conversation_id": "conv-99", "issued_at": datetime.now(timezone.utc).isoformat()}
	sup.callback(_Event("put", "/-NaabbCC", cmd))
	# Two yields: first lets call_soon_threadsafe flush; second runs the created task.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert cmd in received


@pytest.mark.asyncio
async def test_combine_listener_idempotent(monkeypatch):
	"""Calling start_combine_command_listener twice does not register a second listener."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	async def _noop(cmd, ack=None): pass

	await be.start_combine_command_listener(_noop)
	await be.start_combine_command_listener(_noop)

	assert len(_FakeSupervised.instances) == 1, "second call must not create a second listener"


@pytest.mark.asyncio
async def test_force_end_listener_idempotent(monkeypatch):
	"""Calling start_force_end_command_listener twice does not register a second listener."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	async def _noop(cmd, ack=None): pass

	await be.start_force_end_command_listener(_noop)
	await be.start_force_end_command_listener(_noop)

	assert len(_FakeSupervised.instances) == 1, "second call must not create a second listener"


@pytest.mark.asyncio
async def test_stale_command_is_dropped_with_notice_not_dispatched(monkeypatch):
	"""Decided 2026-06-11: a command older than the TTL is deleted with a
	phone-visible notice, never executed and never silently swallowed."""
	from datetime import datetime, timedelta, timezone
	from unittest.mock import AsyncMock
	from server.command_freshness import COMMAND_TTL_SECONDS

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)
	be.send_text = AsyncMock()

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	received: list[dict] = []

	async def _handler(cmd, ack=None):
		received.append(cmd)

	await be.start_combine_command_listener(_handler)
	sup = _FakeSupervised.instances[0]

	stale_iso = (datetime.now(timezone.utc) - timedelta(seconds=COMMAND_TTL_SECONDS + 600)).isoformat()
	stale = {"source_conversation_id": "a", "target_conversation_id": "b", "issued_at": stale_iso}
	sup.callback(_Event("put", "/-Nstale1", stale))
	await asyncio.sleep(0.05)  # drain call_soon_threadsafe + to_thread

	assert received == [], "stale command must not execute"
	be.send_text.assert_awaited_once()
	notice = be.send_text.await_args.args[0]
	assert "stale" in notice.lower() and stale_iso in notice
	# The stale entry is still deleted so it cannot replay forever.
	from server import firebase as fb_module
	assert any(c.args == ("combine_commands/-Nstale1",) for c in fb_module.db.reference.call_args_list)
	fb_module.db.reference.return_value.delete.assert_called()


@pytest.mark.asyncio
async def test_redelivered_command_is_dispatched_once(monkeypatch):
	"""At-least-once with per-run dedupe: the same push-id arriving again
	(snapshot replay after a listener reconnect) must not re-execute."""
	from datetime import datetime, timezone

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	received: list[dict] = []

	async def _handler(cmd, ack=None):
		received.append(cmd)

	await be.start_combine_command_listener(_handler)
	sup = _FakeSupervised.instances[0]

	cmd = {
		"source_conversation_id": "a",
		"target_conversation_id": "b",
		"issued_at": datetime.now(timezone.utc).isoformat(),
	}
	sup.callback(_Event("put", "/-Ndup1", cmd))
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	# The same push-id arrives again: an SSE redelivery, or (post-implementation)
	# a reconnect snapshot re-delivering an entry whose delete hadn't landed.
	sup.callback(_Event("put", "/-Ndup1", cmd))
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	# And once more in snapshot shape; the dedupe key is the push-id either way.
	sup.callback(_Event("put", "/", {"-Ndup1": cmd}))
	await asyncio.sleep(0)
	await asyncio.sleep(0)

	assert received == [cmd], f"dedupe must suppress redeliveries; got {len(received)} dispatches"


@pytest.mark.asyncio
async def test_spawn_command_deleted_before_handler_runs(monkeypatch):
	"""M1: spawn is the one non-idempotent command handler — it mints a fresh
	conversation and launches a process every time it runs. With the default
	at-least-once delivery (delete scheduled AFTER dispatch), a crash between
	dispatch and the fire-and-forget delete replays the command from the next
	restart snapshot and double-spawns. So the spawn listener must delete the
	command (and await the delete) BEFORE running the handler."""
	from datetime import datetime, timezone

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	from server import firebase as fb_module
	order: list[tuple] = []

	def _ref(path):
		m = MagicMock()
		m.delete.side_effect = lambda p=path: order.append(("delete", p))
		return m

	fb_module.db.reference.side_effect = _ref

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	async def _handler(cmd, ack=None):
		order.append(("handler", cmd.get("type")))

	await be.start_spawn_command_listener(_handler)
	sup = _FakeSupervised.instances[0]
	assert sup.path == "spawn_commands"

	cmd = {"type": "fresh", "project": "X", "issued_at": datetime.now(timezone.utc).isoformat()}
	sup.callback(_Event("put", "/-Nspawn1", cmd))
	await asyncio.sleep(0.05)  # drain call_soon_threadsafe + to_thread

	assert ("delete", "spawn_commands/-Nspawn1") in order, "spawn command must be deleted"
	assert ("handler", "fresh") in order, "handler must run"
	assert order.index(("delete", "spawn_commands/-Nspawn1")) < order.index(("handler", "fresh")), \
		"delete must commit before the handler launches (at-most-once for non-idempotent spawn)"


@pytest.mark.asyncio
async def test_listener_callback_never_calls_run_in_executor(monkeypatch):
	"""M32: the listener callback runs on the Firebase SDK thread;
	loop.run_in_executor is not thread-safe from there. Every loop-affined
	call must bounce through call_soon_threadsafe."""
	from datetime import datetime, timezone
	from unittest.mock import MagicMock

	fake_loop = MagicMock()
	be = _make_backend(monkeypatch, fake_loop)

	import server.firebase_supervisor as sup_mod
	_FakeSupervised.instances.clear()
	monkeypatch.setattr(sup_mod, "SupervisedListener", _FakeSupervised)

	handler = MagicMock()  # sync mock: the coroutine is never awaited here
	await be.start_combine_command_listener(handler)
	sup = _FakeSupervised.instances[0]

	cmd = {
		"source_conversation_id": "a",
		"target_conversation_id": "b",
		"issued_at": datetime.now(timezone.utc).isoformat(),
	}
	sup.callback(_Event("put", "/-Nbridge1", cmd))

	fake_loop.run_in_executor.assert_not_called()
	assert fake_loop.call_soon_threadsafe.call_count >= 2, \
		"both the dispatch and the delete must bounce via call_soon_threadsafe"


@pytest.mark.asyncio
async def test_schedule_command_delete_bridges_from_a_foreign_thread(monkeypatch):
	"""The honest M32 lock: fire the delete helper from a non-loop thread
	(like the Firebase SDK listener thread) and verify the delete lands via
	the loop bounce."""
	import threading

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)

	t = threading.Thread(target=lambda: be._schedule_command_delete("combine_commands", "cmd-x"))
	t.start()
	t.join()
	await asyncio.sleep(0.05)  # let call_soon_threadsafe + to_thread run

	from server import firebase as fb_module
	assert any(c.args == ("combine_commands/cmd-x",) for c in fb_module.db.reference.call_args_list)
	fb_module.db.reference.return_value.delete.assert_called()
