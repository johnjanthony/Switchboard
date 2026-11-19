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
