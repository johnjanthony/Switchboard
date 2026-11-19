"""Conversation member state changes, including the dormancy routine the session-end path shares."""

from __future__ import annotations
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
	from server.registry import Conversation, ConversationMember

LOST_REASON = "lost"
RESTARTED_REASON = "restarted"


def lost_message(sender: str) -> str:
	return f"{sender}'s session was lost (no heartbeat); member is now dormant."


def back_message(sender: str) -> str:
	return f"{sender}'s session is back; member is alive again."


async def make_member_dormant(
	registry,
	session_id: str,
	*,
	reason: str,
	message_for: Callable[[ConversationMember], str],
	now: Callable[[], str],
	backend=None,
	permanent: bool = False,
	wake_own: bool = True,
) -> tuple[str, Conversation | None, ConversationMember | None]:
	"""Unbind the session, mark its member dormant, record a system message and wake the waiters.

	Returns (status, conversation, member); status is "ok", "no_binding", "conv_missing" or
	"no_member", and the conversation and member are None unless it is "ok".
	wake_own=False leaves queued any wait entry owned by the session's own member.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	conversation_id = registry.unbind_session(session_id)
	if conversation_id is None:
		return "no_binding", None, None
	conv = registry.conversations.get(conversation_id)
	if conv is None:
		return "conv_missing", None, None
	target = conv.members_active.get(session_id)
	if target is None:
		return "no_member", None, None
	target.alive = False
	target.session_ended_at = now()
	target.session_end_reason = reason
	if permanent:
		target.session_lost_permanently = True
	if reason == LOST_REASON:
		registry.lost_dormant.add(session_id)
	dormancy_msg = {
		"seq": len(conv.messages),
		"type": "system",
		"sender": "<system>",
		"text": message_for(target),
		"timestamp": now(),
	}
	conv.messages.append(dormancy_msg)
	if backend is not None:
		_spawn_bg(
			backend.write_conversation_member(conversation_id, target),
			label=f"fb_write_member_dormant:{conversation_id}:{target.sender}",
		)
		_spawn_bg(
			backend.write_conversation_message(conversation_id, dormancy_msg),
			label=f"fb_write_dormancy_msg:{conversation_id}:{target.sender}",
		)
	# Wake every waiter blocked in message_and_await_agent on this conv. Each
	# live waiter gets its full unseen delta composed via _compose_wake_payload
	# - which includes the just-appended dormancy line - NOT just the dormancy
	# text: the cursor jump below would otherwise hide any not-yet-seen message
	# from every future wake and join log (REV-111). The empty-payload fallback
	# guards the degenerate already-caught-up case so a wake never resolves
	# with an empty string.
	# Don't hold the conv.lock when resolving futures: future callbacks may
	# schedule async work, and we're not inside the lock here (the route
	# handler in main.py doesn't take it). Snapshot then clear, then resolve.
	from server.conversation_ops import _compose_wake_payload
	to_wake = list(conv.wait_queue)
	conv.wait_queue.clear()
	for entry in to_wake:
		member = entry.get("member")
		fut = entry.get("future")
		if not wake_own and member is target:
			if fut is not None and not fut.done():
				conv.wait_queue.append(entry)
			continue
		if fut is None or fut.done():
			continue
		if member is None:
			fut.set_result(dormancy_msg["text"])
			continue
		payload = _compose_wake_payload(conv, member, entry.get("waiting_kind", "msg_and_await"))
		fut.set_result(payload or dormancy_msg["text"])
		# Advance the woken member's cursor past everything just delivered so
		# its next wake delta does not re-include it (parity with
		# _wake_one_from in conversation_ops.py). F-70.
		member.last_seen_seq = len(conv.messages)
	return "ok", conv, target
