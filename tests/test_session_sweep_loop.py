"""Session staleness sweeper: the per-tick body and the supervised loop wrapper."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from server.registry import Conversation, ConversationMember, Registry
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
async def test_sweep_flips_lost_antigravity_member_dormant_and_resumable():
	"""Antigravity has no SessionEnd hook, so the silence sweep is the only signal
	that can flip its conversation member dormant. When the sweep marks an agy
	session lost, the bound member must become dormant (alive=False) and resumable
	(not permanently lost), and the session must be unbound - otherwise phone /
	Operator resume finds no resumable members and silently no-ops."""
	from server.gateway.dispatch import _session_sweep_once

	reg = _reg()
	reg.record_session_start("agy-sess", cwd="C:/Work/client", cli="antigravity")
	store = WidgetSnapshotStore()
	store.rings = {}

	registry = Registry()
	registry.sessions = reg
	conv = Conversation(id="conv-1", title="client (windows)")
	member = ConversationMember(
		cli_session_id="agy-sess", sender="Antigravity Win", cwd="C:/Work/client",
		surface="windows", joined_at=0.0, alive=True,
	)
	conv.members_active["agy-sess"] = member
	registry.conversations["conv-1"] = conv
	registry.bind_session("agy-sess", "conv-1")

	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	now_ts = last + 1000
	store.pushed_at = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()

	await _session_sweep_once(
		reg, store, lost_after_seconds=900, retention_hours=72, now_ts=now_ts, registry=registry,
	)

	assert member.alive is False
	assert member.session_lost_permanently is False
	assert registry.session_to_conversation_id.get("agy-sess") is None


@pytest.mark.asyncio
async def test_sweep_leaves_lost_claude_member_untouched():
	"""The propagation is Antigravity-only: a lost Claude session's member is left
	for the SessionEnd-marker path, not flipped by the silence sweep (avoids
	altering Claude lifecycle as a side effect)."""
	from server.gateway.dispatch import _session_sweep_once

	reg = _reg()
	reg.record_session_start("cc-sess", cwd="C:/Work/X", cli="claude")
	store = WidgetSnapshotStore()
	store.rings = {}

	registry = Registry()
	registry.sessions = reg
	conv = Conversation(id="conv-2", title="X (windows)")
	member = ConversationMember(
		cli_session_id="cc-sess", sender="Claude Win", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=True,
	)
	conv.members_active["cc-sess"] = member
	registry.conversations["conv-2"] = conv
	registry.bind_session("cc-sess", "conv-2")

	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	now_ts = last + 1000
	store.pushed_at = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()

	await _session_sweep_once(
		reg, store, lost_after_seconds=900, retention_hours=72, now_ts=now_ts, registry=registry,
	)

	assert reg.get("cc-sess").state == "lost"
	assert member.alive is True
	assert registry.session_to_conversation_id.get("cc-sess") == "conv-2"


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


@pytest.mark.asyncio
async def test_sweep_once_derives_liveness_from_registry():
	"""A parked (future-less) pending is NOT a liveness proof; a live future or
	a live wait-queue entry is."""
	from server.gateway.dispatch import _session_sweep_once

	reg = _reg()
	for sid in ("s-parked", "s-live", "s-wait"):
		reg.record_session_start(sid, cwd="C:/Work/X")
	reg.upsert_from_hook("s-parked", state="awaiting_human", detail=None, event="PreToolUse")
	reg.upsert_from_hook("s-live", state="awaiting_human", detail=None, event="PreToolUse")
	reg.upsert_from_hook("s-wait", state="awaiting_agent", detail=None, event="PreToolUse")

	registry = Registry()
	registry.add_parked("conv-1", "s-parked", "Claude", "req-p", question="Q?")
	registry.add("conv-2", "s-live", "Claude", "req-l")
	conv = Conversation(id="conv-3", title="t")
	member = ConversationMember(cli_session_id="s-wait", sender="X", cwd="", surface="windows", joined_at=0.0)
	fut = asyncio.get_event_loop().create_future()
	conv.wait_queue.append({"member": member, "future": fut, "waiting_kind": "msg_and_await", "block_position": 0.0})
	registry.conversations["conv-3"] = conv

	store = WidgetSnapshotStore()
	store.pushed_at = "2026-07-06T12:15:00+00:00"
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	await _session_sweep_once(
		reg, store, lost_after_seconds=900, retention_hours=72,
		now_ts=last + 1000, registry=registry,
	)
	assert reg.get("s-parked").state == "lost"
	assert reg.get("s-live").state == "awaiting_human"
	assert reg.get("s-wait").state == "awaiting_agent"


@pytest.mark.asyncio
async def test_marker_health_warning_emitted_once(tmp_path):
	import json as _json
	from server.gateway.dispatch import _maybe_warn_marker_health
	from server.logging_jsonl import JsonlLogger
	reg = _reg()
	reg.presumed_dead_total = 3
	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	await _maybe_warn_marker_health(reg, logger, tmp_path / "session-end")
	await _maybe_warn_marker_health(reg, logger, tmp_path / "session-end")
	lines = [_json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
	warnings = [l for l in lines if l.get("event") == "surface_error" and "session_end_markers_missing" in (l.get("detail") or "")]
	assert len(warnings) == 1
