"""Member state changes that are not a session end."""
import asyncio

import pytest

from server.member_lifecycle import LOST_REASON, lost_message, back_message, make_member_dormant
from server.registry import ConversationMember
from tests.test_cli_session_end import _fixed_now, _setup_conv_with_member


async def _pump():
	for _ in range(5):
		await asyncio.sleep(0)


def test_the_two_messages_are_exact():
	assert lost_message("Claude Win") == "Claude Win's session was lost (no heartbeat); member is now dormant."
	assert back_message("Claude Win") == "Claude Win's session is back; member is alive again."


@pytest.mark.asyncio
async def test_lost_dormancy_unbinds_marks_and_records():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	status, conv, member = await make_member_dormant(
		registry, sid, reason=LOST_REASON, message_for=lambda m: lost_message(m.sender), now=_fixed_now, backend=backend,
	)
	await _pump()
	assert status == "ok" and conv.id == conv_id
	assert member.alive is False and member.session_end_reason == "lost"
	assert member.session_ended_at == _fixed_now() and member.session_lost_permanently is False
	assert sid not in registry.session_to_conversation_id
	assert sid in registry.lost_dormant
	assert conv.messages[-1]["text"] == "Claude's session was lost (no heartbeat); member is now dormant."
	assert backend.member_writes == [(conv_id, member)]
	assert backend.message_writes == [(conv_id, conv.messages[-1])]


@pytest.mark.asyncio
async def test_a_session_end_reason_is_not_entered_in_the_lost_set():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	await make_member_dormant(registry, sid, reason="logout", message_for=lambda m: "x", now=_fixed_now, backend=backend)
	assert registry.lost_dormant == set()


@pytest.mark.asyncio
async def test_wake_own_false_leaves_the_sessions_own_wait_and_wakes_the_rest():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	conv = registry.conversations[conv_id]
	peer = ConversationMember(cli_session_id="sess-B", sender="Peer", cwd="C:/Y", surface="windows", joined_at=0.0)
	conv.members_active["sess-B"] = peer
	loop = asyncio.get_running_loop()
	own, other = loop.create_future(), loop.create_future()
	conv.wait_queue.append({"future": own, "member": conv.members_active[sid], "waiting_kind": "msg_and_await"})
	conv.wait_queue.append({"future": other, "member": peer, "waiting_kind": "msg_and_await"})
	await make_member_dormant(
		registry, sid, reason=LOST_REASON, message_for=lambda m: lost_message(m.sender), now=_fixed_now,
		backend=backend, wake_own=False,
	)
	assert not own.done() and len(conv.wait_queue) == 1
	assert other.done() and "session was lost" in other.result()
	assert peer.last_seen_seq == len(conv.messages)


@pytest.mark.asyncio
async def test_unbound_session_reports_no_binding_and_changes_nothing():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	registry.unbind_session(sid)
	status, conv, member = await make_member_dormant(
		registry, sid, reason=LOST_REASON, message_for=lambda m: "x", now=_fixed_now, backend=backend,
	)
	assert (status, conv, member) == ("no_binding", None, None)
	assert registry.conversations[conv_id].members_active[sid].alive is True


from server.member_lifecycle import sweep_lost_members
from server.session_registry import SessionRegistry


class _Log:
	def __init__(self):
		self.infos, self.errors = [], []

	async def info(self, text):
		self.infos.append(text)

	async def surface_error(self, text):
		self.errors.append(text)


def _roster(registry):
	clock = {"t": 1000.0}
	sessions = SessionRegistry(now=_fixed_now, mono=lambda: clock["t"])
	registry.sessions = sessions
	return sessions, clock


async def _sweep(registry, sessions, backend, log):
	return await sweep_lost_members(
		registry, sessions, backend, log, poll_lost_after_seconds=90, lost_after_seconds=900, now=_fixed_now,
	)


def _bound_live_member():
	"""A member bound at t=1000 whose session then showed life at t=1010."""
	registry, backend, conv_id, sid = _setup_conv_with_member()
	sessions, clock = _roster(registry)
	registry.bind_session(sid, conv_id)
	clock["t"] = 1010.0
	sessions.record_session_start(sid, cwd="C:/X")
	return registry, backend, conv_id, sid, sessions, clock


@pytest.mark.asyncio
async def test_a_lost_session_silent_past_the_poll_threshold_goes_dormant():
	registry, backend, conv_id, sid, sessions, clock = _bound_live_member()
	sessions.get(sid).state = "lost"
	clock["t"] = 1101.0
	log = _Log()
	assert await _sweep(registry, sessions, backend, log) == [sid]
	member = registry.conversations[conv_id].members_active[sid]
	assert member.alive is False and member.session_end_reason == "lost"
	assert log.infos == [f"member_dormant_lost: session={sid} conversation={conv_id} sender=Claude via=sweep"]


@pytest.mark.asyncio
async def test_a_lost_session_with_fresh_life_in_any_channel_is_left_alone():
	for sign in (
		lambda s, sid: s.touch_mcp(sid, cwd="C:/X"),
		lambda s, sid: s.apply_telemetry(sid, {}),
		lambda s, sid: s.set_awaiting_approval(sid, _fixed_now()),
	):
		registry, backend, conv_id, sid, sessions, clock = _bound_live_member()
		sessions.get(sid).state = "lost"
		clock["t"] = 1500.0
		sign(sessions, sid)
		sessions.get(sid).state = "lost"
		clock["t"] = 1580.0
		assert await _sweep(registry, sessions, backend, _Log()) == []
		assert registry.conversations[conv_id].members_active[sid].alive is True


@pytest.mark.asyncio
async def test_a_live_record_is_never_grounds():
	registry, backend, conv_id, sid, sessions, clock = _bound_live_member()
	clock["t"] = 9000.0
	assert await _sweep(registry, sessions, backend, _Log()) == []


@pytest.mark.asyncio
async def test_a_launch_that_never_came_up_times_out_at_900_seconds():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	sessions, clock = _roster(registry)
	sessions.record_session_start(sid, cwd="C:/X")
	sessions.record_session_end(sid, reason="logout", ended_at=_fixed_now())
	clock["t"] = 2000.0
	registry.bind_session(sid, conv_id)
	clock["t"] = 2899.0
	assert await _sweep(registry, sessions, backend, _Log()) == []
	clock["t"] = 2901.0
	log = _Log()
	assert await _sweep(registry, sessions, backend, log) == [sid]
	assert log.infos[0].endswith("via=launch_timeout")


@pytest.mark.asyncio
async def test_a_bound_member_with_no_record_times_out_the_same_way():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	sessions, clock = _roster(registry)
	registry.bind_session(sid, conv_id)
	clock["t"] = 1901.0
	assert await _sweep(registry, sessions, backend, _Log()) == [sid]


@pytest.mark.asyncio
async def test_a_launch_whose_session_started_in_time_is_never_touched():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	sessions, clock = _roster(registry)
	sessions.record_session_start(sid, cwd="C:/X")
	sessions.record_session_end(sid, reason="logout", ended_at=_fixed_now())
	clock["t"] = 2000.0
	registry.bind_session(sid, conv_id)
	clock["t"] = 2020.0
	sessions.record_session_start(sid, cwd="C:/X")
	clock["t"] = 9000.0
	assert await _sweep(registry, sessions, backend, _Log()) == []


@pytest.mark.asyncio
async def test_parked_and_background_asks_survive_and_the_own_wait_stays_queued():
	registry, backend, conv_id, sid, sessions, clock = _bound_live_member()
	conv = registry.conversations[conv_id]
	registry.add(conv_id, sid, "Claude", "req-1", msg_id="m-1", question="q")
	registry.find_by_request_id(conv_id, "req-1").future = None
	registry.add_background(conv_id, sid, "Claude", "req-2", question="bg")
	own = asyncio.get_running_loop().create_future()
	conv.wait_queue.append({"future": own, "member": conv.members_active[sid], "waiting_kind": "msg_and_await"})
	sessions.get(sid).state = "lost"
	clock["t"] = 1101.0
	assert await _sweep(registry, sessions, backend, _Log()) == [sid]
	assert registry.find_by_request_id(conv_id, "req-1") is not None
	assert registry.find_by_request_id(conv_id, "req-2") is not None
	assert not own.done() and len(conv.wait_queue) == 1


@pytest.mark.asyncio
async def test_a_member_of_an_ended_conversation_is_skipped():
	registry, backend, conv_id, sid, sessions, clock = _bound_live_member()
	sessions.get(sid).state = "lost"
	clock["t"] = 1101.0
	registry.conversations[conv_id].state = "ended"
	assert await _sweep(registry, sessions, backend, _Log()) == []


@pytest.mark.asyncio
async def test_a_session_whose_record_is_not_gone_is_never_timed_out_by_launch():
	registry, backend, conv_id, sid = _setup_conv_with_member()
	sessions, clock = _roster(registry)
	sessions.record_session_start(sid, cwd="C:/X")
	sessions.upsert_from_hook(sid, state="awaiting_human", detail=None, event="PreToolUse")
	clock["t"] = 2000.0
	registry.bind_session(sid, conv_id)
	clock["t"] = 2901.0
	assert await _sweep(registry, sessions, backend, _Log()) == []
	assert registry.conversations[conv_id].members_active[sid].alive is True


from server.member_lifecycle import close_revive_window, revive_member


async def _lost_dormant():
	registry, backend, conv_id, sid, sessions, clock = _bound_live_member()
	await make_member_dormant(
		registry, sid, reason=LOST_REASON, message_for=lambda m: lost_message(m.sender), now=_fixed_now, backend=backend,
	)
	await _pump()
	backend.member_writes.clear()
	backend.message_writes.clear()
	return registry, backend, conv_id, sid, sessions


@pytest.mark.asyncio
async def test_revive_restores_the_member_and_the_binding():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	log = _Log()
	assert await revive_member(registry, sessions, backend, log, sid, trigger="poll", now=_fixed_now) is True
	await _pump()
	conv = registry.conversations[conv_id]
	member = conv.members_active[sid]
	assert member.alive is True and member.session_end_reason is None and member.session_ended_at is None
	assert registry.session_to_conversation_id[sid] == conv_id
	assert sid not in registry.lost_dormant
	assert conv.messages[-1]["text"] == "Claude's session is back; member is alive again."
	assert backend.member_writes == [(conv_id, member)]
	assert log.infos == [f"member_revived: session={sid} conversation={conv_id} sender=Claude trigger=poll"]


@pytest.mark.asyncio
async def test_a_session_not_in_the_set_costs_no_scan():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	registry.lost_dormant.clear()

	class _NoScan(dict):
		def values(self):
			raise AssertionError("scanned conversations")

	registry.conversations = _NoScan(registry.conversations)
	assert await revive_member(registry, sessions, backend, _Log(), sid, trigger="poll") is False


@pytest.mark.asyncio
async def test_a_bound_session_is_not_touched_and_leaves_the_set():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	registry.bind_session(sid, "conv-elsewhere")
	assert await revive_member(registry, sessions, backend, _Log(), sid, trigger="status") is False
	assert sid not in registry.lost_dormant
	assert registry.conversations[conv_id].members_active[sid].alive is False


@pytest.mark.asyncio
async def test_an_ended_conversation_and_a_permanently_lost_member_are_not_candidates():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	registry.conversations[conv_id].state = "ended"
	assert await revive_member(registry, sessions, backend, _Log(), sid, trigger="poll") is False
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	registry.conversations[conv_id].members_active[sid].session_lost_permanently = True
	assert await revive_member(registry, sessions, backend, _Log(), sid, trigger="poll") is False


@pytest.mark.asyncio
async def test_two_signs_of_life_at_once_revive_once():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	conv = registry.conversations[conv_id]
	before = len(conv.messages)
	await conv.lock.acquire()
	first = asyncio.create_task(revive_member(registry, sessions, backend, _Log(), sid, trigger="poll", now=_fixed_now))
	second = asyncio.create_task(
		revive_member(registry, sessions, backend, _Log(), sid, trigger="tool_call", now=_fixed_now)
	)
	for _ in range(5):
		await asyncio.sleep(0)
	assert not first.done() and not second.done()
	conv.lock.release()
	results = await asyncio.gather(first, second)
	assert sorted(results) == [False, True]
	assert len(conv.messages) == before + 1


@pytest.mark.asyncio
async def test_a_new_life_rewrites_the_reason_and_stops_any_revive():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	assert await close_revive_window(registry, backend, _Log(), sid) is True
	await _pump()
	member = registry.conversations[conv_id].members_active[sid]
	assert member.alive is False and member.session_end_reason == "restarted"
	assert backend.member_writes == [(conv_id, member)]
	assert await revive_member(registry, sessions, backend, _Log(), sid, trigger="tool_call") is False
	assert sid not in registry.session_to_conversation_id


from server.member_lifecycle import repair_members


def _alive_member_with_record(state):
	registry, backend, conv_id, sid = _setup_conv_with_member()
	registry.unbind_session(sid)
	sessions = SessionRegistry(now=lambda: "2026-11-01T00:00:00+00:00")
	if state is not None:
		sessions.hydrate_record({"cli_session_id": sid, "state": state, "last_event_at": "2026-10-13T08:00:00+00:00"})
	return registry, conv_id, sid, sessions


def test_repair_makes_a_member_with_a_terminal_or_missing_record_dormant():
	for state in ("lost", "ended", None):
		registry, conv_id, sid, sessions = _alive_member_with_record(state)
		changed = repair_members(registry, sessions)
		member = registry.conversations[conv_id].members_active[sid]
		assert changed == [(conv_id, member)]
		assert member.alive is False and member.session_end_reason == "lost"
		assert member.session_ended_at == ("2026-10-13T08:00:00+00:00" if state else None)
		assert sid in registry.lost_dormant


def test_repair_leaves_a_member_with_a_live_record_alone():
	registry, conv_id, sid, sessions = _alive_member_with_record("idle")
	assert repair_members(registry, sessions) == []
	assert registry.conversations[conv_id].members_active[sid].alive is True


def test_repair_rebuilds_the_set_from_a_stored_lost_member_without_reporting_it():
	registry, conv_id, sid, sessions = _alive_member_with_record("lost")
	member = registry.conversations[conv_id].members_active[sid]
	member.alive, member.session_end_reason = False, "lost"
	assert repair_members(registry, sessions) == []
	assert sid in registry.lost_dormant


from server.member_lifecycle import idle_end_candidates, idle_since
from server.registry import Conversation, Registry

DAY = 86400.0
NOW = 1_800_000_000.0


def _dead_conversation(last_activity_days_ago=20.0, ended_iso=None):
	registry = Registry()
	conv = Conversation(id="conv-1", title="t", created_at=NOW - 40 * DAY, last_activity_at=NOW - last_activity_days_ago * DAY)
	member = ConversationMember(cli_session_id="s", sender="Claude", cwd="", surface="windows", joined_at=0.0, alive=False)
	member.session_ended_at = ended_iso
	conv.members_active["s"] = member
	registry.conversations["conv-1"] = conv
	return registry, conv, member


def _ids(registry, days=14):
	return [c.id for c, _ in idle_end_candidates(registry, idle_days=days, now_ts=NOW)]


def test_a_dead_conversation_idle_past_the_knob_is_a_candidate():
	registry, conv, member = _dead_conversation()
	[(found, days)] = idle_end_candidates(registry, idle_days=14, now_ts=NOW)
	assert found is conv and round(days) == 20


@pytest.mark.asyncio
async def test_each_condition_blocks_on_its_own():
	registry, conv, member = _dead_conversation()
	member.alive = True
	assert _ids(registry) == []

	registry, conv, member = _dead_conversation()
	registry.add("conv-1", "s", "Claude", "req-1")
	registry.find_by_request_id("conv-1", "req-1").future = None
	assert _ids(registry) == []

	registry, conv, member = _dead_conversation()
	conv.wait_queue.append({"future": asyncio.get_running_loop().create_future(), "member": member})
	assert _ids(registry) == []

	registry, conv, member = _dead_conversation(last_activity_days_ago=13.0)
	assert _ids(registry) == []


def test_the_clock_runs_from_the_later_of_activity_and_last_dormancy():
	from datetime import datetime, timezone
	recent = datetime.fromtimestamp(NOW - 3600, tz=timezone.utc).isoformat()
	registry, conv, member = _dead_conversation(last_activity_days_ago=30.0, ended_iso=recent)
	assert _ids(registry) == []


def test_zero_disables_and_a_hidden_or_memberless_conversation_counts():
	registry, conv, member = _dead_conversation()
	assert _ids(registry, days=0) == []
	conv.hidden = True
	assert _ids(registry) == ["conv-1"]
	conv.members_active.clear()
	assert _ids(registry) == ["conv-1"]


def test_a_conversation_with_no_usable_timestamp_is_never_a_candidate():
	registry, conv, member = _dead_conversation(ended_iso="not a date")
	conv.last_activity_at = 0.0
	conv.created_at = 0.0
	assert idle_since(conv) is None
	assert _ids(registry) == []


def test_an_ended_conversation_is_not_a_candidate():
	registry, conv, member = _dead_conversation()
	conv.state = "ended"
	assert _ids(registry) == []


def test_a_conversation_with_a_session_bound_to_it_is_not_a_candidate():
	registry, conv, member = _dead_conversation()
	registry.bind_session("fresh-spawn", "conv-1")
	assert _ids(registry) == []


@pytest.mark.asyncio
async def test_revive_is_refused_for_a_session_the_roster_records_as_ended():
	registry, backend, conv_id, sid, sessions = await _lost_dormant()
	sessions.record_session_end(sid, reason="logout", ended_at=_fixed_now())
	log = _Log()
	conv = registry.conversations[conv_id]
	before = len(conv.messages)
	assert await revive_member(registry, sessions, backend, log, sid, trigger="poll", now=_fixed_now) is False
	await _pump()
	member = conv.members_active[sid]
	assert member.alive is False and sid not in registry.session_to_conversation_id
	assert len(conv.messages) == before and log.infos == []
