"""Tests for ClaudeStatusService action handling (injected fetch + fake publish)."""

import asyncio
from datetime import datetime, timezone

import pytest

from server.claude_status import ClaudeStatus, ClaudeStatusService

T0 = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)


def _service(levels):
	"""Service whose fetch returns the queued levels in order (last repeats)."""
	seq = list(levels)
	published = []

	async def fetch():
		level = seq.pop(0) if len(seq) > 1 else seq[0]
		return ClaudeStatus(level, f"desc-{level}", [], T0)

	async def publish(view):
		published.append(view)

	def fake_spawn(coro, label):
		coro.close()  # discard the background loop coroutine un-run: no runaway task in unit tests
		return None

	svc = ClaudeStatusService(publish=publish, fetch=fetch, interval_seconds=0, spawn=fake_spawn)
	return svc, published


@pytest.mark.asyncio
async def test_check_operational_publishes_idle_view():
	svc, published = _service(["operational"])
	view = await svc.check()
	assert view["watch_state"] == "idle"
	assert view["button"] == "check"
	assert published and published[-1]["watch_state"] == "idle"


@pytest.mark.asyncio
async def test_check_degraded_enters_watching():
	svc, published = _service(["major"])
	view = await svc.check()
	assert view["watch_state"] == "watching"
	assert view["level"] == "major"
	assert view["button"] == "stop"
	assert published[-1]["watch_state"] == "watching"


@pytest.mark.asyncio
async def test_stop_acknowledges_to_idle():
	svc, published = _service(["major"])
	await svc.check()
	view = await svc.stop()
	assert view["watch_state"] == "idle"
	assert view["button"] == "check"


@pytest.mark.asyncio
async def test_poll_loop_runs_until_resolved_and_ends():
	"""With the REAL background spawn (not the discarding fake), a watch started by
	check() must run the poll loop, observe the next operational fetch, publish
	resolved_unacked, and let the task end on its own. This covers the loop's
	fetch/apply/publish/break wiring that the unit fakes and the live E2E cannot
	reach (the live endpoint cannot be forced degraded -> resolved)."""
	seq = ["major", "operational"]
	published = []

	async def fetch():
		level = seq.pop(0) if len(seq) > 1 else seq[0]
		return ClaudeStatus(level, f"desc-{level}", [], T0)

	async def publish(view):
		published.append(view)

	svc = ClaudeStatusService(publish=publish, fetch=fetch, interval_seconds=0)
	view = await svc.check()
	assert view["watch_state"] == "watching"
	# Let the real background poll loop run to completion (interval 0 -> immediate).
	await asyncio.wait_for(svc._task, timeout=1.0)
	assert svc._task.done()
	assert published[-1]["watch_state"] == "resolved_unacked"
