"""SessionEnd handler: marks a conversation member dormant when its CLI session ends.

Called by:
- the SessionEnd marker-file sweep (dispatch_session_end_markers in server/gateway/dispatch.py)

reason values:
- "logout"        — agent typed /exit; member dormant but resumable
- "clear"         — /clear command; member permanently lost (no resume)
- "compact"       — context compaction; member permanently lost (no resume)
- "other"         — fallback for anything else; member dormant but resumable
"""

from __future__ import annotations
from typing import Callable

PERMANENTLY_LOST_REASONS = {"clear", "compact"}


async def handle_session_end(
	registry,
	session_id: str,
	reason: str,
	now: Callable[[], str],
	backend=None,
	logger=None,
	session_registry=None,
) -> None:
	"""Mark a conversation member dormant when its CLI session ends.

	backend: optional ConversationStore -- if provided, Firebase writes are issued:
	  write_conversation_member (updated alive=False fields) and write_conversation_message
	  for the dormancy system message.
	logger: optional logger with a surface_error(msg) coroutine -- if provided,
	  the three silent early-return paths emit a loud log instead of returning silently.
	session_registry: optional SessionRegistry -- if provided, its record is marked
	  ended BEFORE the unbind below, so even a session with no conversation binding
	  still gets its record ended.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	if session_registry is not None:
		session_registry.record_session_end(session_id, reason=reason, ended_at=now())
	conversation_id = registry.unbind_session(session_id)
	if conversation_id is None:
		if logger is not None:
			await logger.surface_error(
				f"session_end_no_binding: session {session_id} not bound to any conversation (reason={reason})"
			)
		return
	conv = registry.conversations.get(conversation_id)
	if conv is None:
		if logger is not None:
			await logger.surface_error(
				f"session_end_conv_missing: session {session_id} bound to {conversation_id} but conversation absent (reason={reason})"
			)
		return
	target = conv.members_active.get(session_id)
	if target is None:
		if logger is not None:
			await logger.surface_error(
				f"session_end_no_member: session {session_id} bound to {conversation_id} but no member matches (reason={reason}); dormancy skipped"
			)
		return
	target.alive = False
	target.session_ended_at = now()
	target.session_end_reason = reason
	if reason in PERMANENTLY_LOST_REASONS:
		target.session_lost_permanently = True
	from datetime import datetime, timezone
	dormancy_msg = {
		"seq": len(conv.messages),
		"type": "system",
		"sender": "<system>",
		"text": f"{target.sender}'s session ended ({reason}); member is now dormant.",
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
		fut = entry.get("future")
		if fut is None or fut.done():
			continue
		member = entry.get("member")
		if member is None:
			fut.set_result(dormancy_msg["text"])
			continue
		payload = _compose_wake_payload(conv, member, entry.get("waiting_kind", "msg_and_await"))
		fut.set_result(payload or dormancy_msg["text"])
		# Advance the woken member's cursor past everything just delivered so
		# its next wake delta does not re-include it (parity with
		# _wake_one_from in conversation_ops.py). F-70.
		member.last_seen_seq = len(conv.messages)
	# Terminal handling for pendings owned by the departed session (REV-001).
	# Live futures are cancelled - freeing the asker instead of a 24h wait -
	# and the awaiting coroutine's CancelledError arm performs the Firebase
	# cancel, so no direct write happens here. Parked records (future=None,
	# T-001) have no coroutine: on a resumable end (logout/other) the question
	# SURVIVES - it stays parked and phone-answerable, and the answer notice
	# queues for delivery on resume. On a permanent end (clear/compact)
	# nothing can ever deliver the answer, so the record terminates properly:
	# Firebase cancel + replay memory, the pair the old remove()-only path
	# forgot (ghost questions that resurrected across restarts).
	from server.gateway.pending_lifecycle import terminate_pending
	for pending in registry.pending_for_conversation(conversation_id):
		if pending.cli_session_id != session_id:
			continue
		parked = pending.future is None
		if parked and reason not in PERMANENTLY_LOST_REASONS:
			continue
		await terminate_pending(
			registry, backend, logger, pending,
			mark_cancelled=parked,
			remember_resolved=parked,
		)
