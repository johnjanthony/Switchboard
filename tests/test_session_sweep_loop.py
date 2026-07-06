"""Session staleness sweeper: the per-tick body and the supervised loop wrapper."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from server.session_registry import SessionRegistry
from server.widget_snapshot import WidgetSnapshotStore


def _reg():
	return SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")


@pytest.mark.asyncio
async def test_sweep_once_marks_lost_when_rings_fresh():
	"""A fresh Watchtower push (pushed_at within WATCHTOWER_FRESH_SECONDS of now)
	means ring absence is trustworthy, so a long-silent session gets marked lost."""
	from server.gateway.dispatch import _session_sweep_once

	reg = _reg()
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	store = WidgetSnapshotStore()
	store.rings = {}

	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	now_ts = last + 1000
	# Fresh relative to now_ts (the sweep's clock), not real wall-clock time.
	store.pushed_at = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
	pruned = await _session_sweep_once(
		reg, store, lost_after_seconds=900, retention_hours=72, now_ts=now_ts,
	)
	assert pruned == []
	assert reg.get("sess-OLD").state == "lost"


@pytest.mark.asyncio
async def test_sweep_once_suspends_lost_marking_when_rings_stale():
	"""A stale (or absent) Watchtower push means ring absence proves nothing, so
	lost-marking is suspended rather than guessed."""
	from server.gateway.dispatch import _session_sweep_once

	reg = _reg()
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	store = WidgetSnapshotStore()
	store.pushed_at = "2026-01-01T00:00:00+00:00"
	store.rings = {}

	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	pruned = await _session_sweep_once(
		reg, store, lost_after_seconds=900, retention_hours=72, now_ts=last + 1000,
	)
	assert pruned == []
	assert reg.get("sess-OLD").state == "idle"


@pytest.mark.asyncio
async def test_sweep_once_defaults_now_ts_to_wall_clock():
	"""now_ts=None falls back to time.time() rather than requiring the caller
	to always supply one (the production loop never passes it)."""
	from server.gateway.dispatch import _session_sweep_once

	reg = SessionRegistry()  # real wall-clock now()
	reg.record_session_start("sess-FRESH", cwd="C:/Work/X")
	store = WidgetSnapshotStore()

	pruned = await _session_sweep_once(reg, store, lost_after_seconds=900, retention_hours=72)
	assert pruned == []
	assert reg.get("sess-FRESH").state == "idle"  # far too young to be marked lost


@pytest.mark.asyncio
async def test_dispatch_loop_runs_and_can_be_cancelled(tmp_path):
	from server.gateway.dispatch import dispatch_session_sweep
	from server.logging_jsonl import JsonlLogger
	from tests.conftest import _make_loop_supervisor

	reg = _reg()
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	store = WidgetSnapshotStore()
	store.pushed_at = datetime.now(timezone.utc).isoformat()

	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_loop_supervisor(None, logger, "dispatch_session_sweep")

	# now_ts is not injectable through the loop, so drive staleness via
	# last_event_at set just past lost_after_seconds but well inside retention,
	# so the first tick marks it lost without a second tick pruning it away.
	past = datetime.now(timezone.utc).timestamp() - 901
	reg.get("sess-OLD").last_event_at = datetime.fromtimestamp(past, tz=timezone.utc).isoformat()

	# Interval long enough that only the first (immediate) tick fires before we
	# assert and cancel - avoids a second tick pruning the now-terminal record.
	task = asyncio.create_task(
		dispatch_session_sweep(
			reg, store, logger, supervisor,
			lost_after_seconds=900, retention_hours=72, interval=10.0,
		)
	)
	for _ in range(5):
		await asyncio.sleep(0)

	assert reg.get("sess-OLD").state == "lost"

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
