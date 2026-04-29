"""Tests for the bg-task tracking primitive."""

from __future__ import annotations

import asyncio

import pytest

from server.gateway.bg_tasks import _BG_TASKS, _spawn_bg


@pytest.fixture(autouse=True)
def _clean_bg_tasks():
	"""Ensure each test starts with an empty tracking set."""
	_BG_TASKS.clear()
	yield
	_BG_TASKS.clear()


@pytest.mark.asyncio
async def test_spawn_bg_adds_task_to_tracking_set():
	async def _work():
		await asyncio.sleep(0.05)

	task = _spawn_bg(_work(), label="test:add")
	assert task in _BG_TASKS
	assert task.get_name() == "test:add"

	await task
	# done callback runs in the loop; yield once so it gets scheduled.
	await asyncio.sleep(0)
	assert task not in _BG_TASKS


@pytest.mark.asyncio
async def test_spawn_bg_removes_on_completion():
	async def _work():
		return "done"

	task = _spawn_bg(_work(), label="test:complete")
	result = await task
	await asyncio.sleep(0)
	assert result == "done"
	assert task not in _BG_TASKS


@pytest.mark.asyncio
async def test_spawn_bg_removes_on_exception():
	async def _boom():
		raise RuntimeError("boom")

	task = _spawn_bg(_boom(), label="test:exception")
	with pytest.raises(RuntimeError, match="boom"):
		await task
	await asyncio.sleep(0)
	assert task not in _BG_TASKS


@pytest.mark.asyncio
async def test_spawn_bg_removes_on_cancellation():
	async def _slow():
		await asyncio.sleep(60)

	task = _spawn_bg(_slow(), label="test:cancel")
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task
	await asyncio.sleep(0)
	assert task not in _BG_TASKS


@pytest.mark.asyncio
async def test_spawn_bg_concurrent_tasks_all_tracked_until_done():
	async def _work(n: int):
		await asyncio.sleep(0.01 * n)
		return n

	tasks = [_spawn_bg(_work(i), label=f"test:concurrent:{i}") for i in range(5)]
	# All five should currently be in the set.
	assert all(t in _BG_TASKS for t in tasks)

	results = await asyncio.gather(*tasks)
	await asyncio.sleep(0)
	assert results == [0, 1, 2, 3, 4]
	assert all(t not in _BG_TASKS for t in tasks)


def test_spawn_bg_requires_running_loop():
	"""Outside a running event loop, asyncio.create_task raises RuntimeError;
	_spawn_bg propagates that."""
	async def _noop():
		pass

	coro = _noop()
	try:
		with pytest.raises(RuntimeError):
			_spawn_bg(coro, label="test:no_loop")
	finally:
		# Close the unawaited coroutine to suppress the ResourceWarning.
		coro.close()
