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


@pytest.mark.asyncio
async def test_drain_bg_tasks_waits_for_inflight():
	"""B1: drain_bg_tasks waits for outstanding background tasks so their
	fire-and-forget Firebase writes flush before the loop closes on shutdown."""
	from server.gateway.bg_tasks import drain_bg_tasks

	flushed = []

	async def _slow_write():
		await asyncio.sleep(0.03)
		flushed.append(True)

	task = _spawn_bg(_slow_write(), label="test:drain")
	remaining = await drain_bg_tasks(timeout=2.0)
	assert task.done(), "drain must wait for the in-flight task to finish"
	assert flushed == [True], "the background write must have completed"
	assert remaining == 0


@pytest.mark.asyncio
async def test_drain_bg_tasks_reports_count_on_timeout():
	"""B1: a task that outlives the bounded timeout is reported, not awaited
	forever (shutdown must not hang on a stuck write)."""
	from server.gateway.bg_tasks import drain_bg_tasks

	async def _hang():
		await asyncio.sleep(30)

	task = _spawn_bg(_hang(), label="test:drain_timeout")
	try:
		remaining = await drain_bg_tasks(timeout=0.01)
		assert remaining >= 1
		assert not task.done()
	finally:
		task.cancel()
		with pytest.raises(asyncio.CancelledError):
			await task


@pytest.mark.asyncio
async def test_spawn_bg_logs_task_exception(caplog):
	"""B2: a background task that raises is logged (it used to be swallowed
	silently by the done-callback, hiding failed Firebase writes)."""
	import logging

	async def _boom():
		raise ValueError("kaboom-bg")

	with caplog.at_level(logging.ERROR, logger="server.gateway.bg_tasks"):
		task = _spawn_bg(_boom(), label="test:logged_exc")
		with pytest.raises(ValueError):
			await task
		await asyncio.sleep(0)  # let the done-callback run

	logged = [r for r in caplog.records if r.name == "server.gateway.bg_tasks"]
	assert logged, "the background-task exception must be logged by bg_tasks"
	assert any("kaboom-bg" in r.getMessage() or "test:logged_exc" in r.getMessage() for r in logged)


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
