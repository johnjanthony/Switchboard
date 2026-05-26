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
) -> None:
	"""Mark a conversation member dormant when its CLI session ends.

	backend: optional ConversationStore — if provided, Firebase writes are issued:
	  write_conversation_member (updated alive=False fields) and write_conversation_message
	  for the dormancy system message.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	conversation_id = registry.unbind_session(session_id)
	if conversation_id is None:
		return
	conv = registry.conversations.get(conversation_id)
	if conv is None:
		return
	target = None
	for member in conv.members_active.values():
		if member.cli_session_id == session_id:
			target = member
			break
	if target is None:
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
	# Future: wake FIFO-oldest blocked peer + cancel pending ask_human futures for this session.
	# These hooks depend on helpers that don't exist yet; later tasks add them.
