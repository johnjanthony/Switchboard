"""Session-fallback rule: when a session is removed from a conversation,
where does its routing binding go?"""

from __future__ import annotations

import uuid
from typing import Literal


def compute_fallback(
	session_id: str,
	home_conversation_id: str | None,
	home_state: Literal["active", "ended"] | None,
	global_away_mode: bool,
) -> tuple[Literal["unbind", "rebind_home", "create_new"], str | None]:
	"""Returns (action, target_conversation_id_or_None).

	- "unbind"       — session has no conversation; subsequent xxx_human calls
	                   get at-desk-redirected.
	- "rebind_home"  — re-bind to the existing home conversation.
	- "create_new"   — create a fresh single-agent Active conversation;
	                   update the session's home pointer to it.
	"""
	if not global_away_mode:
		return ("unbind", None)
	if home_conversation_id is not None and home_state == "active":
		return ("rebind_home", home_conversation_id)
	return ("create_new", None)


def apply_fallback(registry, session_id: str, backend=None) -> None:
	"""Apply session fallback routing when a session leaves a conversation.

	backend: optional ConversationStore — if provided, Firebase writes are issued:
	  - "unbind": remove_session_binding for the session
	  - "rebind_home": no Firebase write (home pointer unchanged)
	  - "create_new": write_conversation_meta + set_session_home for the new conv
	"""
	import time
	from server.registry import Conversation
	from server.gateway.bg_tasks import _spawn_bg
	home_id = registry.session_home_conversation_id.get(session_id)
	home_state = None
	if home_id and home_id in registry.conversations:
		home_state = registry.conversations[home_id].state
	action, target = compute_fallback(
		session_id=session_id,
		home_conversation_id=home_id,
		home_state=home_state,
		global_away_mode=registry._global_away,
	)
	if action == "unbind":
		registry.unbind_session(session_id)
		if backend is not None:
			_spawn_bg(
				backend.remove_session_binding(session_id),
				label=f"fb_remove_session_binding:{session_id}",
			)
	elif action == "rebind_home":
		registry.unbind_session(session_id)
		registry.bind_session(session_id, target)
		# No Firebase write: home pointer hasn't changed; only routing is updated.
	else:  # "create_new"
		new_id = "conv-" + uuid.uuid4().hex
		now = time.time()
		new_conv = Conversation(id=new_id, title="(home)")
		new_conv.created_at = now
		new_conv.last_activity_at = now
		registry.conversations[new_id] = new_conv
		registry.unbind_session(session_id)
		registry.bind_session(session_id, new_id)
		registry.set_session_home(session_id, new_id)
		if backend is not None:
			_spawn_bg(
				backend.write_conversation_meta(
					new_id,
					title="(home)",
					state="active",
					continued_from=None,
					created_at=now,
					last_activity_at=now,
					ended_at=None,
					hidden=False,
				),
				label=f"fb_write_conv_meta:{new_id}",
			)
			_spawn_bg(
				backend.set_session_home(session_id, new_id),
				label=f"fb_set_session_home:{session_id}:{new_id}",
			)
