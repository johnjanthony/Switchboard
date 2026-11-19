"""SessionEnd handler: marks a conversation member dormant when its CLI session ends.

Called by:
- POST /session_end (_build_session_end_route in server/main.py), the Claude Code mod's session.end hook

reason values:
- "logout"        — agent typed /exit; member dormant but resumable
- "clear"         — /clear command; member permanently lost (no resume)
- "compact"       — context compaction; member permanently lost (no resume)
- "other"         — fallback for anything else; member dormant but resumable
"""

from __future__ import annotations
from typing import Callable

from server.member_lifecycle import make_member_dormant

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
	  the conv_missing and no_member early returns surface an error instead of returning
	  silently; the no_binding return is logged through its info(msg) coroutine, when it has one.
	session_registry: optional SessionRegistry -- if provided, its record is marked
	  ended BEFORE the unbind below, so even a session with no conversation binding
	  still gets its record ended.
	"""
	if session_registry is not None:
		session_registry.record_session_end(session_id, reason=reason, ended_at=now())
	conversation_id = registry.session_to_conversation_id.get(session_id)
	status, _conv, _member = await make_member_dormant(
		registry, session_id, reason=reason,
		message_for=lambda m: f"{m.sender}'s session ended ({reason}); member is now dormant.",
		now=now, backend=backend, permanent=reason in PERMANENTLY_LOST_REASONS,
	)
	if status != "ok":
		if logger is not None:
			if status == "no_binding":
				if hasattr(logger, "info"):
					await logger.info(
						f"session_end_no_binding: session {session_id} not bound to any conversation (reason={reason})"
					)
			elif status == "conv_missing":
				await logger.surface_error(
					f"session_end_conv_missing: session {session_id} bound to {conversation_id} but conversation absent (reason={reason})"
				)
			else:
				await logger.surface_error(
					f"session_end_no_member: session {session_id} bound to {conversation_id} but no member matches (reason={reason}); dormancy skipped"
				)
		return
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
