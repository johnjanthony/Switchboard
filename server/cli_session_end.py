"""SessionEnd handler: marks a conversation member dormant when its CLI session ends.

Called by:
- POST /cli-session/end (HTTP route in server/main.py)
- (Future) the dispatcher when other code paths surface a SessionEnd

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
) -> None:
	"""Mark a conversation member dormant when its CLI session ends.

	backend: optional ConversationStore -- if provided, Firebase writes are issued:
	  write_conversation_member (updated alive=False fields) and write_conversation_message
	  for the dormancy system message.
	logger: optional logger with a surface_error(msg) coroutine -- if provided,
	  the three silent early-return paths emit a loud log instead of returning silently.
	"""
	from server.gateway.bg_tasks import _spawn_bg
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
	target = None
	for member in conv.members_active.values():
		if member.cli_session_id == session_id:
			target = member
			break
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
	# Wake every waiter blocked in message_and_await_agent on this conv.
	# Resolve their futures with the dormancy_msg text so the peer surfaces
	# from its wait_for and can decide what to do next on its own turn.
	# Don't hold the conv.lock when resolving futures: future callbacks may
	# schedule async work, and we're not inside the lock here (the route
	# handler in main.py doesn't take it). Snapshot then clear, then resolve.
	to_wake = list(conv.wait_queue)
	conv.wait_queue.clear()
	for entry in to_wake:
		fut = entry.get("future")
		if fut is not None and not fut.done():
			fut.set_result(dormancy_msg["text"])
			# Advance the woken member's cursor past the dormancy message so
			# its next wake delta does not re-include the dormancy line
			# (parity with _wake_one_from in conversation_ops.py). F-70.
			member = entry.get("member")
			if member is not None:
				member.last_seen_seq = len(conv.messages)
	# Also surface dormancy to any mint-path opener blocked on open_peer_future
	# (e.g. the opener's session itself ended, or a peer's session ended before
	# they fully joined). Returning the dormancy text lets the opener decide
	# what to do next instead of hanging until timeout.
	opener_future = conv.open_peer_future
	if opener_future is not None and not opener_future.done():
		opener_future.set_result(dormancy_msg["text"])
		conv.open_peer_future = None
	# Cancel any pending ask_human futures owned by this departed member —
	# their answer can never arrive (the agent's session is gone), so freeing
	# the future immediately avoids a 24h _TIMEOUT wait if the agent ever
	# reconnects mid-block. Match by routing identity (cli_session_id), not by
	# sender string: ask_human keys the pending by the RAW agent-supplied
	# sender, which differs from the member's disambiguated sender on a
	# same-name collision (e.g. pending 'Claude' vs member 'Claude 2'), so a
	# sender-string compare would silently miss the cancellation (M2). Fall
	# back to the sender match for any legacy pending with no recorded session.
	for pending in registry.pending_for_conversation(conversation_id):
		owned = (
			pending.cli_session_id == session_id
			if pending.cli_session_id is not None
			else pending.sender == target.sender
		)
		if owned:
			registry.remove(conversation_id, pending.sender, request_id=pending.request_id)
