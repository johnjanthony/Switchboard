"""Conversation retention sweep (WP-10): ended conversations past the
retention horizon lose their index card plus /messages and /answers nodes
and are evicted from the in-memory registry. Active conversations are
never touched."""
import asyncio
import time

import pytest

from server.gateway.dispatch import _conversation_sweep_once, dispatch_conversation_sweep
from server.registry import Registry, Conversation
from tests.conftest import _make_loop_supervisor
from tests.test_hydration import make_logger


class _SweepBackend:
	def __init__(self, metas: dict[str, dict]):
		self._metas = metas
		self.deleted: list[str] = []

	async def list_conversation_ids(self):
		return list(self._metas.keys())

	async def get_conversation_meta(self, conv_id):
		return self._metas.get(conv_id)

	async def delete_conversation_nodes(self, conv_id):
		self.deleted.append(conv_id)


HOURS_73_AGO = time.time() - 73 * 3600


@pytest.mark.asyncio
async def test_sweep_deletes_ended_past_horizon_and_evicts_registry():
	registry = Registry()
	registry.conversations["conv-old"] = Conversation(id="conv-old", title="t", state="ended", ended_at=HOURS_73_AGO)
	backend = _SweepBackend({"conv-old": {"state": "ended", "ended_at": HOURS_73_AGO}})
	deleted = await _conversation_sweep_once(registry, backend, make_logger(), retention_hours=72)
	assert deleted == ["conv-old"]
	assert backend.deleted == ["conv-old"]
	assert "conv-old" not in registry.conversations


@pytest.mark.asyncio
async def test_sweep_never_touches_active_regardless_of_age():
	registry = Registry()
	backend = _SweepBackend({"conv-a": {"state": "active", "last_activity_at": time.time() - 999 * 3600}})
	deleted = await _conversation_sweep_once(registry, backend, make_logger(), retention_hours=72)
	assert deleted == [] and backend.deleted == []


@pytest.mark.asyncio
async def test_sweep_keeps_recent_ended():
	backend = _SweepBackend({"conv-new": {"state": "ended", "ended_at": time.time() - 3600}})
	deleted = await _conversation_sweep_once(Registry(), backend, make_logger(), retention_hours=72)
	assert deleted == []


@pytest.mark.asyncio
async def test_sweep_falls_back_to_last_activity_at():
	backend = _SweepBackend({"conv-x": {"state": "ended", "last_activity_at": HOURS_73_AGO}})
	deleted = await _conversation_sweep_once(Registry(), backend, make_logger(), retention_hours=72)
	assert deleted == ["conv-x"]


@pytest.mark.asyncio
async def test_sweep_skips_missing_timestamps_and_degenerate_meta():
	backend = _SweepBackend({"conv-y": {"state": "ended"}, "conv-z": None})
	deleted = await _conversation_sweep_once(Registry(), backend, make_logger(), retention_hours=72)
	assert deleted == []


@pytest.mark.asyncio
async def test_sweep_skips_when_in_memory_copy_is_active():
	# RTDB says ended but the live registry disagrees: never delete under a
	# conversation the server believes is active.
	registry = Registry()
	registry.conversations["conv-live"] = Conversation(id="conv-live", title="t", state="active")
	backend = _SweepBackend({"conv-live": {"state": "ended", "ended_at": HOURS_73_AGO}})
	deleted = await _conversation_sweep_once(registry, backend, make_logger(), retention_hours=72)
	assert deleted == [] and backend.deleted == []
	assert "conv-live" in registry.conversations


@pytest.mark.asyncio
async def test_dispatch_loop_runs_and_can_be_cancelled():
	registry = Registry()
	backend = _SweepBackend({"conv-old": {"state": "ended", "ended_at": HOURS_73_AGO}})
	logger = make_logger()
	supervisor = _make_loop_supervisor(None, logger, "dispatch_conversation_sweep")
	task = asyncio.create_task(dispatch_conversation_sweep(
		registry, backend, logger, supervisor, retention_hours=72, interval=10.0,
	))
	for _ in range(5):
		await asyncio.sleep(0)
	assert backend.deleted == ["conv-old"]
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task
