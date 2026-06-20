"""P0-3 (H02): handle_force_end must clear the conversation's pending ask_human
futures and mark their question records cancelled; otherwise an agent blocked in
ask_human when John force-ends from the phone stays blocked for the full 24h
timeout.

T-145 (2026-06-15): the future is now RESOLVED with the
'__CONVERSATION_ENDED__\n(force-ended)' sentinel rather than cancelled. A
cancelled future surfaces on the agent's MCP client as a transport error, which
the agent retries (re-stranding it / minting orphan state); a resolved future
returns the sentinel as a normal value so the agent stops without retrying."""

from __future__ import annotations

import time

import pytest

from server.gateway.dispatch import handle_force_end
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


class CancelTrackingBackend(RecordingBackend):
	def __init__(self) -> None:
		super().__init__()
		self.cancelled_questions: list[tuple[str, str]] = []

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		self.cancelled_questions.append((conversation_id, request_id))


def _registry_with_conv(conv_id: str = "conv-fe") -> Registry:
	registry = Registry()
	conv = Conversation(id=conv_id, title="FE")
	member = ConversationMember(
		cli_session_id="s-1", sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=time.time(),
	)
	conv.members_active["Claude"] = member
	registry.conversations[conv_id] = conv
	registry.bind_session("s-1", conv_id)
	return registry


@pytest.mark.asyncio
async def test_force_end_resolves_pending_ask_human_future():
	registry = _registry_with_conv()
	backend = CancelTrackingBackend()

	# Agent blocked in ask_human: a pending future exists for (conv, sender)
	future = registry.add("conv-fe", "Claude", request_id="req-1", msg_id="msg-1")
	assert not future.done()

	await handle_force_end(registry, "conv-fe", backend=backend)

	# The future resolves promptly with the terminal sentinel (not cancelled,
	# which the agent would read as a transport error and retry), not stranded
	# for 24h (T-145).
	assert future.done()
	assert not future.cancelled(), "force-end must resolve the future, not cancel it"
	assert future.result() == "__CONVERSATION_ENDED__\n(force-ended)"
	# No pending entry survives for the ended conversation
	assert registry.pending_for_conversation("conv-fe") == []
	# The question's Firebase record is marked cancelled (mark_question_cancelled
	# also clears its pending_questions record; firebase.py:223-225)
	assert ("conv-fe", "req-1") in backend.cancelled_questions


@pytest.mark.asyncio
async def test_force_end_decrements_pending_badge_via_mirror():
	"""The pending mirror is how the phone's pending_responses badge tracks
	registry state; cancellation on force-end must fire the decrement."""
	registry = _registry_with_conv()
	backend = CancelTrackingBackend()
	deltas: list[tuple[str, int]] = []
	registry.set_pending_mirror(lambda conv_id, delta: deltas.append((conv_id, delta)))

	registry.add("conv-fe", "Claude", request_id="req-1", msg_id="msg-1")
	await handle_force_end(registry, "conv-fe", backend=backend)

	# +1 on add, -1 on force-end cancellation: badge nets to zero
	assert sum(delta for cid, delta in deltas if cid == "conv-fe") == 0
