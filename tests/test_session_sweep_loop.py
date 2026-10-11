"""Session staleness sweeper: the per-tick body and the supervised loop wrapper."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from server.registry import Conversation, ConversationMember, Registry
from server.session_registry import SessionRegistry

LAST = datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()


def _reg():
	return SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")


async def _sweep_once(reg, **overrides):
	from server.gateway.dispatch import _session_sweep_once
	args = dict(lost_after_seconds=900, poll_lost_after_seconds=90, retention_hours=72, now_ts=LAST + 1000)
	args.update(overrides)
	return await _session_sweep_once(reg, **args)


@pytest.mark.asyncio
async def test_sweep_once_marks_a_silent_never_polled_session_lost():
	reg = _reg()
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	pruned = await _sweep_once(reg)
	assert pruned == []
	assert reg.get("sess-OLD").state == "lost"


@pytest.mark.asyncio
async def test_sweep_once_in_grace_marks_nothing_lost():
	reg = _reg()
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	await _sweep_once(reg, in_grace=True)
	assert reg.get("sess-OLD").state == "idle"


@pytest.mark.asyncio
async def test_sweep_flips_lost_antigravity_member_dormant_and_resumable():
	"""Antigravity has no SessionEnd hook, so the silence sweep is the only signal
	that can flip its conversation member dormant. When the sweep marks an agy
	session lost, the bound member must become dormant (alive=False) and resumable
	(not permanently lost), and the session must be unbound - otherwise phone /
	Operator resume finds no resumable members and silently no-ops."""
	reg = _reg()
	reg.record_session_start("agy-sess", cwd="C:/Work/client", cli="antigravity")
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

	await _sweep_once(reg, registry=registry)

	assert member.alive is False
	assert member.session_lost_permanently is False
	assert registry.session_to_conversation_id.get("agy-sess") is None


@pytest.mark.asyncio
async def test_sweep_leaves_lost_claude_member_untouched():
	"""The propagation is Antigravity-only: a lost Claude session's member is left
	for the SessionEnd path, not flipped by the silence sweep (avoids altering
	Claude lifecycle as a side effect)."""
	reg = _reg()
	reg.record_session_start("cc-sess", cwd="C:/Work/X", cli="claude")
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

	await _sweep_once(reg, registry=registry)

	assert reg.get("cc-sess").state == "lost"
	assert member.alive is True
	assert registry.session_to_conversation_id.get("cc-sess") == "conv-2"


@pytest.mark.asyncio
async def test_sweep_once_defaults_now_ts_to_wall_clock():
	"""now_ts=None falls back to time.time() rather than requiring the caller
	to always supply one."""
	from server.gateway.dispatch import _session_sweep_once
	reg = SessionRegistry()  # real wall-clock now()
	reg.record_session_start("sess-FRESH", cwd="C:/Work/X")
	pruned = await _session_sweep_once(reg, lost_after_seconds=900, poll_lost_after_seconds=90, retention_hours=72)
	assert pruned == []
	assert reg.get("sess-FRESH").state == "idle"  # far too young to be marked lost


@pytest.mark.asyncio
async def test_dispatch_loop_runs_and_can_be_cancelled(tmp_path):
	from server.gateway.dispatch import dispatch_session_sweep
	from server.logging_jsonl import JsonlLogger
	from tests.conftest import _make_loop_supervisor

	reg = _reg()
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	supervisor = _make_loop_supervisor(None, logger, "dispatch_session_sweep")

	# now_ts is not injectable through the loop, so drive staleness via
	# last_event_at set just past lost_after_seconds but well inside retention,
	# so the first tick marks it lost without a second tick pruning it away.
	past = datetime.now(timezone.utc).timestamp() - 901
	reg.get("sess-OLD").last_event_at = datetime.fromtimestamp(past, tz=timezone.utc).isoformat()

	# grace_seconds=0 so the first tick judges at once; the interval is long
	# enough that only that tick fires before we assert and cancel.
	task = asyncio.create_task(
		dispatch_session_sweep(
			reg, logger, supervisor,
			lost_after_seconds=900, poll_lost_after_seconds=90, retention_hours=72,
			interval=10.0, grace_seconds=0,
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

	await _sweep_once(reg, registry=registry)
	assert reg.get("s-parked").state == "lost"
	assert reg.get("s-live").state == "awaiting_human"
	assert reg.get("s-wait").state == "awaiting_agent"


class _TitleBackend:
	def __init__(self) -> None:
		self.titles: list = []

	async def write_conversation_title(self, conv_id, title, title_source=None):
		self.titles.append((conv_id, title, title_source))


@pytest.mark.asyncio
async def test_sweep_zeroes_an_expired_quota_window_and_publishes_it():
	from unittest.mock import AsyncMock, MagicMock
	from server.quota_store import QuotaStore

	quota = QuotaStore()
	quota.apply([{"kind": "five_hour", "percent_used": 40, "resets_at": "2026-07-06T12:30:00+00:00"}], "t1")
	backend = MagicMock()
	backend.write_quota = AsyncMock()
	await _sweep_once(_reg(), now_ts=LAST + 3600, backend=backend, quota_store=quota)
	backend.write_quota.assert_awaited_once_with({"five_hour": {"percent_used": 0, "resets_at": None}, "received_at": "t1"})


@pytest.mark.asyncio
async def test_sweep_reoffers_live_session_titles_to_their_conversations():
	"""A session that joins a conversation after its title arrived still gets
	the title adopted at the sweep's next tick."""
	from tests.conftest import make_active_conversation

	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.apply_telemetry("s1", {"title": {"name": "Joined late", "source": "ai-title"}})
	registry = Registry()
	registry.sessions = reg
	conv = make_active_conversation(conversation_id="conv-1", member_session_id="s1")
	conv.title_source = "default"
	registry.conversations["conv-1"] = conv
	registry.bind_session("s1", "conv-1")
	backend = _TitleBackend()
	await _sweep_once(reg, now_ts=LAST + 10, registry=registry, backend=backend)
	assert conv.title == "Joined late"
	assert backend.titles == [("conv-1", "Joined late", "session")]
