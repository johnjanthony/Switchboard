"""Tests for start_combine_command_listener and start_force_end_command_listener."""

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

	# Fire the initial bulk-load event (path == "/") — must be ignored.
	sup.callback(_Event("put", "/", {"old_key": {"source_conversation_id": "a", "target_conversation_id": "b"}}))
	await asyncio.sleep(0)  # allow any tasks to drain
	assert received == []

	# Fire a new-child event (path == "/<push_id>").
	cmd = {"source_conversation_id": "conv-1", "target_conversation_id": "conv-2", "issued_at": "2026-01-01T00:00:00Z"}
	sup.callback(_Event("put", "/-Nxyz123", cmd))
	# Two yields: first lets call_soon_threadsafe flush; second runs the created task.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert received == [cmd]


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

	# Fire the initial bulk-load event — must be ignored.
	sup.callback(_Event("put", "/", {"old_key": {"conversation_id": "conv-old"}}))
	await asyncio.sleep(0)
	assert received == []

	# Fire a new-child event.
	cmd = {"conversation_id": "conv-99", "issued_at": "2026-01-01T00:00:00Z"}
	sup.callback(_Event("put", "/-NaabbCC", cmd))
	# Two yields: first lets call_soon_threadsafe flush; second runs the created task.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert received == [cmd]


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
