"""Tests for FirebaseBackend._on_spawn_decision listener payload handling.

The phone writes the spawn-collision decision as a dict {"action": ...} at
spawn_collisions/{spawn_id}/decision, matching the bulk-respond convention.
The listener accepts only this shape; older string-only writes are rejected
to enforce the contract.
"""

from __future__ import annotations

import asyncio
import pytest

from server.firebase import FirebaseBackend


class _FakeBackend(FirebaseBackend):
	"""Subclass that bypasses __init__ — only the listener callback is exercised."""

	def __init__(self) -> None:
		self._loop = asyncio.get_running_loop()
		self._spawn_decision_future: asyncio.Future[dict] | None = None
		self._spawn_decision_listener = None
		self._logger = None


class _Event:
	def __init__(self, path: str, data, event_type: str = "put") -> None:
		self.event_type = event_type
		self.path = path
		self.data = data


@pytest.mark.asyncio
async def test_dict_decision_at_path_resolves_future():
	"""Phone writes setValue(mapOf("action" to action)) at .../decision."""
	backend = _FakeBackend()
	backend._spawn_decision_future = asyncio.get_running_loop().create_future()

	backend._on_spawn_decision(_Event(path="/decision", data={"action": "clear"}))

	await asyncio.sleep(0)  # let call_soon_threadsafe run
	assert backend._spawn_decision_future.done()
	assert backend._spawn_decision_future.result() == {"action": "clear"}


@pytest.mark.asyncio
async def test_root_write_with_decision_dict_child_resolves():
	"""Atomic write of the entire spawn_collisions/{id} node with decision as a dict child."""
	backend = _FakeBackend()
	backend._spawn_decision_future = asyncio.get_running_loop().create_future()

	backend._on_spawn_decision(_Event(path="/", data={
		"cwd": "c:/work/foo",
		"decision": {"action": "cancel"},
	}))

	await asyncio.sleep(0)
	assert backend._spawn_decision_future.done()
	assert backend._spawn_decision_future.result() == {"action": "cancel"}


@pytest.mark.asyncio
async def test_string_decision_at_path_is_rejected():
	"""Legacy string-only shape (resolve_collision API) must NOT resolve the future —
	the contract is dict-only to match bulk-respond and leave room for additional fields."""
	backend = _FakeBackend()
	backend._spawn_decision_future = asyncio.get_running_loop().create_future()

	backend._on_spawn_decision(_Event(path="/decision", data="continue"))

	await asyncio.sleep(0)
	assert not backend._spawn_decision_future.done()


@pytest.mark.asyncio
async def test_root_write_without_decision_does_not_resolve():
	"""The initial dialog write (path='', data has no 'decision' field) must not fire."""
	backend = _FakeBackend()
	backend._spawn_decision_future = asyncio.get_running_loop().create_future()

	backend._on_spawn_decision(_Event(path="/", data={
		"cwd": "c:/work/foo",
		"channel_title": "Old Session",
	}))

	await asyncio.sleep(0)
	assert not backend._spawn_decision_future.done()


@pytest.mark.asyncio
async def test_unrelated_path_does_not_resolve():
	"""Writes to other children of the spawn_collisions/{id} node must not fire the future."""
	backend = _FakeBackend()
	backend._spawn_decision_future = asyncio.get_running_loop().create_future()

	backend._on_spawn_decision(_Event(path="/cwd", data="c:/work/foo"))

	await asyncio.sleep(0)
	assert not backend._spawn_decision_future.done()


@pytest.mark.asyncio
async def test_non_put_event_ignored():
	backend = _FakeBackend()
	backend._spawn_decision_future = asyncio.get_running_loop().create_future()

	backend._on_spawn_decision(_Event(path="/decision", data={"action": "continue"}, event_type="patch"))

	await asyncio.sleep(0)
	assert not backend._spawn_decision_future.done()


@pytest.mark.asyncio
async def test_no_active_future_is_safe():
	"""If no poll is in flight, the listener must not crash."""
	backend = _FakeBackend()
	# _spawn_decision_future stays None
	backend._on_spawn_decision(_Event(path="/decision", data={"action": "continue"}))
	await asyncio.sleep(0)
	# No assertion — surviving without exception is the test
