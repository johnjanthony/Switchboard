"""Conversation retention sweep (WP-10): ended conversations past the
retention horizon lose their index card plus /messages and /answers nodes
and are evicted from the in-memory registry. Active conversations are
never touched."""
import asyncio
import time
from datetime import datetime, timezone

import pytest

from server.gateway.dispatch import (
	_conversation_sweep_once, dispatch_conversation_sweep, _prune_admin_notifications_once, _idle_end_once,
)
from server.registry import Registry, Conversation, ConversationMember
from tests.conftest import _make_loop_supervisor
from tests.test_hydration import make_logger


def _iso(epoch: float) -> str:
	return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class _SweepBackend:
	def __init__(self, metas: dict[str, dict], admin: dict | None = None):
		self._metas = metas
		self.deleted: list[str] = []
		self._admin = dict(admin or {})
		self.admin_deleted: list[str] = []

	async def list_conversation_ids(self):
		return list(self._metas.keys())

	async def get_conversation_meta(self, conv_id):
		return self._metas.get(conv_id)

	async def delete_conversation_nodes(self, conv_id):
		self.deleted.append(conv_id)

	async def list_admin_notifications(self):
		return dict(self._admin)

	async def delete_admin_notifications(self, keys):
		self.admin_deleted.extend(keys)


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
async def test_sweep_skips_non_numeric_ended_at():
	# A non-numeric ended_at string must not crash the sweep: the ValueError arm
	# of the except (TypeError, ValueError) catches it and the conversation is skipped.
	backend = _SweepBackend({"conv-bad": {"state": "ended", "ended_at": "not-a-timestamp"}})
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
async def test_dispatch_loop_runs_conversation_and_admin_passes_and_can_be_cancelled():
	registry = Registry()
	backend = _SweepBackend(
		{"conv-old": {"state": "ended", "ended_at": HOURS_73_AGO}},
		admin={"k_old": {"timestamp": _iso(time.time() - 8 * 24 * 3600)}},
	)
	logger = make_logger()
	supervisor = _make_loop_supervisor(None, logger, "dispatch_conversation_sweep")
	task = asyncio.create_task(dispatch_conversation_sweep(
		registry, backend, logger, supervisor,
		retention_hours=72, admin_retention_hours=168, interval=10.0,
	))
	for _ in range(5):
		await asyncio.sleep(0)
	assert backend.deleted == ["conv-old"]
	assert backend.admin_deleted == ["k_old"]
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_admin_prune_deletes_only_stale_entries():
	now = 1_000_000.0
	backend = _SweepBackend({}, admin={
		"k_old": {"text": "old", "timestamp": _iso(now - 8 * 24 * 3600)},   # 8d > 168h
		"k_new": {"text": "new", "timestamp": _iso(now - 3600)},            # 1h
	})
	pruned = await _prune_admin_notifications_once(backend, make_logger(), retention_hours=168, now_ts=now)
	assert pruned == ["k_old"]
	assert backend.admin_deleted == ["k_old"]


@pytest.mark.asyncio
async def test_admin_prune_skips_unparseable_and_nondict_entries():
	now = 1_000_000.0
	backend = _SweepBackend({}, admin={
		"k_bad": {"timestamp": "not-a-date"},
		"k_missing": {"text": "no timestamp"},
		"k_str": "garbage",
	})
	pruned = await _prune_admin_notifications_once(backend, make_logger(), retention_hours=168, now_ts=now)
	assert pruned == []
	assert backend.admin_deleted == []


@pytest.mark.asyncio
async def test_admin_prune_empty_is_noop():
	backend = _SweepBackend({}, admin={})
	pruned = await _prune_admin_notifications_once(backend, make_logger(), retention_hours=168, now_ts=1_000_000.0)
	assert pruned == []
	assert backend.admin_deleted == []


@pytest.mark.asyncio
async def test_retention_counts_from_the_end_not_from_the_last_activity():
	now = time.time()
	backend = _SweepBackend({
		"conv-idle-ended-now": {"state": "ended", "ended_at": now - 3600, "last_activity_at": now - 20 * 86400},
		"conv-ended-long-ago": {"state": "ended", "ended_at": now - 73 * 3600, "last_activity_at": now - 73 * 3600},
	})
	deleted = await _conversation_sweep_once(Registry(), backend, make_logger(), retention_hours=72)
	assert deleted == ["conv-ended-long-ago"]


DAY = 86400.0
NOW = 1_800_000_000.0


class _IdleBackend(_SweepBackend):
	"""A sweep backend that records the writes a force-end makes and reflects them in its meta."""

	def __init__(self, metas=None, admin=None):
		super().__init__(metas if metas is not None else {}, admin)
		self.states: list[tuple] = []
		self.messages: list[tuple] = []
		self.removed: list[tuple] = []
		self.homes: list[tuple] = []

	async def set_conversation_state(self, conv_id, state, *, ended_at=None):
		self.states.append((conv_id, state, ended_at))
		meta = self._metas.setdefault(conv_id, {})
		meta["state"] = state
		if ended_at is not None:
			meta["ended_at"] = ended_at

	async def write_conversation_message(self, conv_id, message):
		self.messages.append((conv_id, message))

	async def remove_conversation_member(self, conv_id, sender):
		self.removed.append((conv_id, sender))

	async def set_session_home(self, *args, **kwargs):
		self.homes.append((args, kwargs))

	async def send_text(self, *args, **kwargs):
		raise AssertionError("an idle end must not push")


def _idle_registry():
	registry = Registry()
	conv = Conversation(id="conv-1", title="t", created_at=NOW - 40 * DAY, last_activity_at=NOW - 20 * DAY)
	member = ConversationMember(cli_session_id="s", sender="Claude", cwd="", surface="windows", joined_at=0.0, alive=False)
	conv.members_active["s"] = member
	registry.conversations["conv-1"] = conv
	registry.lost_dormant.add("s")
	return registry, conv


async def _pump():
	for _ in range(5):
		await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_idle_end_ends_the_conversation_with_its_own_message_and_no_push():
	registry, conv = _idle_registry()
	backend = _IdleBackend()
	logger = make_logger()
	ended = await _idle_end_once(registry, backend, logger, idle_days=14, now_ts=NOW)
	await _pump()
	assert ended == ["conv-1"]
	assert conv.state == "ended" and conv.ended_at
	assert backend.states == [("conv-1", "ended", conv.ended_at)]
	[(_, message)] = backend.messages
	assert message["text"] == "Conversation ended after 14 days with no live session."
	assert "conversation_idle_ended: conv-1 idle_days=20.0" in [c.args[0] for c in logger.info.call_args_list]


@pytest.mark.asyncio
async def test_idle_end_leaves_the_lost_set_clean():
	registry, conv = _idle_registry()
	assert "s" in registry.lost_dormant
	await _idle_end_once(registry, _IdleBackend(), make_logger(), idle_days=14, now_ts=NOW)
	await _pump()
	assert "s" not in registry.lost_dormant


@pytest.mark.asyncio
async def test_one_tick_never_ends_and_deletes_the_same_conversation():
	registry, conv = _idle_registry()
	conv.last_activity_at = time.time() - 20 * DAY

	class _NoEndedAtBackend(_IdleBackend):
		"""Meta keeps the old last_activity_at and gains no ended_at, so only the pass order protects the conversation."""

		async def set_conversation_state(self, conv_id, state, *, ended_at=None):
			self.states.append((conv_id, state, ended_at))
			self._metas[conv_id]["state"] = state

		async def get_conversation_meta(self, conv_id):
			# The spawned state write may not have landed yet; reflect it as RTDB would once it has.
			return {**self._metas[conv_id], "state": registry.conversations[conv_id].state}

	backend = _NoEndedAtBackend({"conv-1": {"state": "active", "last_activity_at": conv.last_activity_at}})
	logger = make_logger()
	supervisor = _make_loop_supervisor(None, logger, "dispatch_conversation_sweep")
	task = asyncio.create_task(dispatch_conversation_sweep(
		registry, backend, logger, supervisor,
		retention_hours=72, admin_retention_hours=168, interval=10.0, idle_end_days=14,
	))
	for _ in range(8):
		await asyncio.sleep(0)
	assert conv.state == "ended"
	assert backend.deleted == []
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


@pytest.mark.asyncio
async def test_a_failing_idle_pass_records_a_crash_and_the_other_passes_still_run(monkeypatch):
	async def boom(*args, **kwargs):
		raise RuntimeError("idle pass broke")

	monkeypatch.setattr("server.gateway.dispatch._idle_end_once", boom)
	backend = _IdleBackend(admin={"k_old": {"timestamp": _iso(time.time() - 8 * 24 * 3600)}})
	logger = make_logger()
	supervisor = _make_loop_supervisor(None, logger, "dispatch_conversation_sweep")
	supervisor._backoff = 0.0  # record_crash sleeps the backoff before the next pass
	task = asyncio.create_task(dispatch_conversation_sweep(
		Registry(), backend, logger, supervisor,
		retention_hours=72, admin_retention_hours=168, interval=10.0, idle_end_days=14,
	))
	for _ in range(30):
		await asyncio.sleep(0)
	assert backend.admin_deleted == ["k_old"]
	assert supervisor._crash_count == 1
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task


def _bound_elsewhere_registry(away: bool):
	registry = Registry()
	registry._global_away = away
	now = time.time()
	a = Conversation(id="conv-A", title="old", created_at=now - 40 * DAY, last_activity_at=now - 20 * DAY)
	a.members_active["S"] = ConversationMember(cli_session_id="S", sender="Claude", cwd="", surface="windows", joined_at=0.0, alive=False)
	b = Conversation(id="conv-B", title="current", created_at=now - DAY, last_activity_at=now)
	b.members_active["S"] = ConversationMember(cli_session_id="S", sender="Claude", cwd="", surface="windows", joined_at=now, alive=True)
	registry.conversations["conv-A"] = a
	registry.conversations["conv-B"] = b
	registry.bind_session("S", "conv-B")
	registry.set_session_home("S", "conv-A")
	return registry


class _ForceEndBackend(_IdleBackend):
	async def write_conversation_meta(self, *args, **kwargs):
		pass

	async def write_conversation_member(self, *args, **kwargs):
		pass


@pytest.mark.asyncio
@pytest.mark.parametrize("away", [True, False])
async def test_ending_a_conversation_leaves_a_session_bound_elsewhere_alone(away):
	registry = _bound_elsewhere_registry(away)
	backend = _ForceEndBackend()
	ended = await _idle_end_once(registry, backend, make_logger(), idle_days=14)
	await _pump()
	assert ended == ["conv-A"]
	assert registry.session_to_conversation_id.get("S") == "conv-B"
	assert sorted(registry.conversations) == ["conv-A", "conv-B"]
	assert "S" not in registry.session_home_conversation_id
	assert (("S", None), {}) in backend.homes


@pytest.mark.asyncio
async def test_a_lost_session_still_lost_in_another_active_conversation_stays_in_the_lost_set():
	registry, conv = _idle_registry()
	conv.members_active["s"].session_end_reason = "lost"
	other = Conversation(id="conv-2", title="t", created_at=NOW, last_activity_at=NOW)
	other.members_active["s"] = ConversationMember(
		cli_session_id="s", sender="Claude", cwd="", surface="windows", joined_at=0.0, alive=False,
	)
	other.members_active["s"].session_end_reason = "lost"
	registry.conversations["conv-2"] = other
	from server.gateway.dispatch import handle_force_end
	await handle_force_end(registry, "conv-1", _IdleBackend(), make_logger())
	await _pump()
	assert "s" in registry.lost_dormant


@pytest.mark.asyncio
async def test_a_candidate_that_stops_qualifying_during_the_pass_is_skipped(monkeypatch):
	registry, conv = _idle_registry()
	second = Conversation(id="conv-2", title="t", created_at=NOW - 40 * DAY, last_activity_at=NOW - 20 * DAY)
	registry.conversations["conv-2"] = second
	import server.gateway.dispatch as dispatch
	real = dispatch.handle_force_end

	async def ending_then_binding(registry, conversation_id, *args, **kwargs):
		await real(registry, conversation_id, *args, **kwargs)
		registry.bind_session("late", "conv-2")

	monkeypatch.setattr(dispatch, "handle_force_end", ending_then_binding)
	ended = await _idle_end_once(registry, _IdleBackend(), make_logger(), idle_days=14, now_ts=NOW)
	assert ended == ["conv-1"]
	assert second.state == "active"
