"""Conversation member state changes, including the dormancy routine the session-end path shares."""

from __future__ import annotations
from typing import Callable, TYPE_CHECKING

from server.clock import now_iso

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


async def sweep_lost_members(
	registry,
	session_registry,
	backend,
	logger,
	*,
	poll_lost_after_seconds: float,
	lost_after_seconds: float,
	now: Callable[[], str] = now_iso,
) -> list[str]:
	"""Make dormant the members of sessions gone silent in every channel; returns the session ids.

	A session that lived since it was bound goes dormant once its record is lost and its last sign of
	life is older than the poll threshold; one that never showed life since it was bound goes dormant
	after the launch threshold, whatever its record says.
	"""
	dormant: list[str] = []
	for sid, conv_id in list(registry.session_to_conversation_id.items()):
		if registry.session_to_conversation_id.get(sid) != conv_id:
			continue
		conv = registry.conversations.get(conv_id)
		member = conv.members_active.get(sid) if conv is not None else None
		if conv is None or conv.state != "active" or member is None or not member.alive:
			continue
		rec = session_registry.get(sid)
		if not (rec is None or rec.state in ("lost", "ended")):
			continue
		life, bound, t = session_registry.last_life(sid), session_registry.bound_at(sid), session_registry.mono()
		if bound is None:
			continue
		if life is not None and life > bound:
			if not (rec is not None and rec.state == "lost" and t - life > poll_lost_after_seconds):
				continue
			via = "sweep"
		else:
			if not t - bound > lost_after_seconds:
				continue
			via = "launch_timeout"
		status, done_conv, done_member = await make_member_dormant(
			registry, sid, reason=LOST_REASON, message_for=lambda m: lost_message(m.sender), now=now,
			backend=backend, wake_own=False,
		)
		if status != "ok":
			continue
		dormant.append(sid)
		if logger is not None:
			await logger.info(
				f"member_dormant_lost: session={sid} conversation={done_conv.id} sender={done_member.sender} via={via}"
			)
	return dormant


def _lost_candidates(registry, session_id: str) -> list:
	found = []
	for conv in registry.conversations.values():
		member = conv.members_active.get(session_id)
		if (
			conv.state == "active" and member is not None and member.alive is False
			and member.session_end_reason == LOST_REASON and not member.session_lost_permanently
		):
			found.append((conv, member))
	return found


async def revive_member(
	registry,
	session_registry,
	backend,
	logger,
	session_id: str,
	*,
	trigger: str,
	now: Callable[[], str] = now_iso,
) -> bool:
	"""Bring back the member of a session made dormant as lost once it shows a sign of life.

	Costs one set lookup for any session that was not lost. Returns True when a member was revived
	and the session rebound to its conversation.
	"""
	if session_id not in registry.lost_dormant:
		return False
	if session_id in registry.session_to_conversation_id:
		registry.lost_dormant.discard(session_id)
		return False
	candidates = _lost_candidates(registry, session_id)
	if not candidates:
		registry.lost_dormant.discard(session_id)
		return False
	from server.gateway.bg_tasks import _spawn_bg
	conv, _ = max(candidates, key=lambda c: (c[1].session_ended_at or "", c[1].joined_at))
	async with conv.lock:
		member = conv.members_active.get(session_id)
		if (
			session_id not in registry.lost_dormant
			or session_id in registry.session_to_conversation_id
			or conv.state != "active"
			or member is None
			or member.alive is not False
			or member.session_end_reason != LOST_REASON
			or member.session_lost_permanently
		):
			return False
		member.alive = True
		member.session_ended_at = None
		member.session_end_reason = None
		member.left_at = None
		registry.bind_session(session_id, conv.id)
		back_msg = {
			"seq": len(conv.messages),
			"type": "system",
			"sender": "<system>",
			"text": back_message(member.sender),
			"timestamp": now(),
		}
		conv.messages.append(back_msg)
		registry.lost_dormant.discard(session_id)
		if backend is not None:
			_spawn_bg(
				backend.write_conversation_member(conv.id, member),
				label=f"fb_write_member_revived:{conv.id}:{member.sender}",
			)
			_spawn_bg(
				backend.write_conversation_message(conv.id, back_msg),
				label=f"fb_write_revive_msg:{conv.id}:{member.sender}",
			)
	if logger is not None:
		await logger.info(
			f"member_revived: session={session_id} conversation={conv.id} sender={member.sender} trigger={trigger}"
		)
	return True


async def close_revive_window(registry, backend, logger, session_id: str) -> bool:
	"""A new session start for a lost-dormant session is a new life: rewrite its reason so it never revives."""
	if session_id not in registry.lost_dormant:
		return False
	registry.lost_dormant.discard(session_id)
	from server.gateway.bg_tasks import _spawn_bg
	for conv in registry.conversations.values():
		member = conv.members_active.get(session_id)
		if (
			conv.state == "active" and member is not None and member.alive is False
			and member.session_end_reason == LOST_REASON
		):
			member.session_end_reason = RESTARTED_REASON
			if backend is not None:
				_spawn_bg(
					backend.write_conversation_member(conv.id, member),
					label=f"fb_write_member_restarted:{conv.id}:{member.sender}",
				)
	return True
