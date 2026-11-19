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
