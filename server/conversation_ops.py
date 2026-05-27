"""Conversation-mutation helpers used by MCP tool handlers and spawn flows.

Helpers in this module mutate Registry state safely (single-event-loop access
assumption from registry.py applies). They are the primary internal API used
by tool handlers in server/gateway/handlers.py to materialize ConversationMember
entries, route sessions, and perform combine/migrate/queue operations.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import TYPE_CHECKING

from server.registry import Conversation, ConversationMember, Registry


_WSL_MOUNT_RE = re.compile(r"^/mnt/[a-z]/", re.IGNORECASE)


def _disambiguate_sender(conv, desired: str) -> str:
	"""If `desired` is already a key in conv.members_active, append a space and
	a number (2, 3, ...) until unique — e.g. 'Claude Win' → 'Claude Win 2'.
	Senders are display labels, so the space-separated form reads naturally on
	the phone bubble attribution. Returns the disambiguated sender string;
	caller must update both the dict key and member.sender to the returned value."""
	if desired not in conv.members_active:
		return desired
	n = 2
	while f"{desired} {n}" in conv.members_active:
		n += 1
	return f"{desired} {n}"


def _infer_surface(cwd: str) -> str:
	"""Returns 'wsl' for /mnt/<letter>/... or /home/... cwds; 'windows' otherwise."""
	if _WSL_MOUNT_RE.match(cwd):
		return "wsl"
	if cwd.startswith("/home/"):
		return "wsl"
	return "windows"


async def _create_active_conversation_for(
	registry: Registry,
	cli_session_id: str,
	cwd: str,
	sender: str,
	backend=None,
	title: str | None = None,
) -> str:
	"""Mint a new Active conversation, add the session as its sole member, bind
	routing, and set home pointer (only if not already set). Returns the new
	conversation_id.

	Called on the FIRST switchboard MCP call from an unbound session.
	backend: optional ConversationStore — if provided, Firebase writes are issued.

	Holds the per-session creation lock so that parallel tool calls from the same
	session (e.g. two concurrent ask_human invocations in one Claude turn) don't
	each create a separate conversation and orphan each other's messages.
	"""
	# Acquire the per-session lock. A concurrent call that already holds the lock
	# will have created + bound the conversation by the time we enter; re-check
	# session_to_conversation_id inside the lock before creating.
	async with registry.session_create_lock(cli_session_id):
		existing = registry.session_to_conversation_id.get(cli_session_id)
		if existing is not None:
			return existing
		return await _create_active_conversation_for_locked(registry, cli_session_id, cwd, sender, backend, title)


async def _create_active_conversation_for_locked(
	registry: Registry,
	cli_session_id: str,
	cwd: str,
	sender: str,
	backend=None,
	title: str | None = None,
) -> str:
	"""Inner implementation called while holding session_create_lock. Do not call directly."""
	from server.gateway.bg_tasks import _spawn_bg
	conv_id = "conv-" + uuid.uuid4().hex
	resolved_title = title if title else f"{sender} · {cwd}"
	conv = Conversation(id=conv_id, title=resolved_title)
	now = time.time()
	conv.created_at = now
	conv.last_activity_at = now
	member = ConversationMember(
		cli_session_id=cli_session_id,
		sender=sender,
		cwd=cwd,
		surface=_infer_surface(cwd),
		joined_at=now,
	)
	conv.members_active[sender] = member
	registry.conversations[conv_id] = conv
	registry.bind_session(cli_session_id, conv_id)
	home_newly_set = cli_session_id not in registry.session_home_conversation_id
	if home_newly_set:
		registry.set_session_home(cli_session_id, conv_id)
	if backend is not None:
		_spawn_bg(
			backend.write_conversation_meta(
				conv_id,
				title=conv.title,
				state="active",
				continued_from=None,
				created_at=now,
				last_activity_at=now,
				ended_at=None,
				hidden=False,
			),
			label=f"fb_write_conv_meta:{conv_id}",
		)
		_spawn_bg(
			backend.write_conversation_member(conv_id, member),
			label=f"fb_write_member:{conv_id}:{sender}",
		)
		if home_newly_set:
			_spawn_bg(
				backend.set_session_home(cli_session_id, conv_id),
				label=f"fb_set_session_home:{cli_session_id}:{conv_id}",
			)
	return conv_id


def _compose_wake_payload(conversation: Conversation, member: ConversationMember, kind: str) -> str:
	"""Compose the payload string a waking member sees when their future resolves.

	kind: "msg_and_await" or "enter"
	- msg_and_await: delta since member.last_seen_seq, excluding member's own emissions
	- enter (already-member intro case): delta since member.last_seen_seq, all senders
	- enter (newly-added): full history (caller's last_seen_seq should be 0)
	"""
	messages_since = conversation.messages[member.last_seen_seq:]
	if kind == "msg_and_await":
		filtered = [m for m in messages_since if m.get("sender") != member.sender]
	else:  # "enter"
		filtered = messages_since
	if not filtered:
		return ""  # nothing new
	lines = []
	for m in filtered:
		sender = m.get("sender", "<unknown>")
		text = m.get("text", "")
		msg_type = m.get("type", "agent_msg")
		if msg_type == "parting":
			lines.append(f"[{sender} left] {text}")
		elif msg_type == "system":
			lines.append(f"[system] {text}")
		else:
			lines.append(f"{sender}: {text}")
	return "\n".join(lines)


async def _add_member(
	registry: Registry,
	conversation_id: str,
	cli_session_id: str,
	sender: str,
	cwd: str,
	backend=None,
) -> None:
	"""Add a new member to an existing conversation, binding the session.

	Caller must hold conv.lock if concurrent safety matters.
	backend: optional ConversationStore — if provided, Firebase writes are issued.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	conv = registry.conversations[conversation_id]
	actual_sender = _disambiguate_sender(conv, sender)
	member = ConversationMember(
		cli_session_id=cli_session_id,
		sender=actual_sender,
		cwd=cwd,
		surface=_infer_surface(cwd),
		joined_at=time.time(),
		last_seen_seq=0,  # new member sees full history on first wake
	)
	conv.members_active[actual_sender] = member
	registry.bind_session(cli_session_id, conversation_id)
	home_newly_set = cli_session_id not in registry.session_home_conversation_id
	if home_newly_set:
		registry.set_session_home(cli_session_id, conversation_id)
	if backend is not None:
		_spawn_bg(
			backend.write_conversation_member(conversation_id, member),
			label=f"fb_write_member:{conversation_id}:{actual_sender}",
		)
		if home_newly_set:
			_spawn_bg(
				backend.set_session_home(cli_session_id, conversation_id),
				label=f"fb_set_session_home:{cli_session_id}:{conversation_id}",
			)


async def _migrate_member(
	registry: Registry,
	source_id: str,
	target_id: str,
	cli_session_id: str,
	sender: str,
	cwd: str,
	backend=None,
) -> None:
	"""Move a member from source conversation to target. Source becomes Ended if
	it has no remaining alive members AND no dormant members. Per the session-fallback
	rule's exemption, the moved session does NOT route back to its home — it goes
	to target. (Migration is a explicit move, not a fallback.)
	backend: optional ConversationStore — if provided, Firebase writes are issued.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	source = registry.conversations[source_id]
	target = registry.conversations[target_id]
	# Find caller in source by cli_session_id (sender may have changed)
	caller_member = None
	old_key = None
	for s, m in source.members_active.items():
		if m.cli_session_id == cli_session_id:
			caller_member = m
			old_key = s
			break
	if caller_member is None:
		# Not in source — fall through to _add_member behavior
		await _add_member(registry, target_id, cli_session_id, sender, cwd, backend=backend)
		return
	# Pop from source, push to target with updated (possibly disambiguated) sender
	del source.members_active[old_key]
	actual_sender = _disambiguate_sender(target, sender)
	caller_member.sender = actual_sender
	caller_member.cwd = cwd
	caller_member.surface = _infer_surface(cwd)
	caller_member.last_seen_seq = 0  # treat as new member of target (full history)
	target.members_active[actual_sender] = caller_member
	registry.bind_session(cli_session_id, target_id)
	# If source has no alive members AND no dormant members → end source
	source_ended = False
	if not source.members_active:
		source.state = "ended"
		source.ended_at = time.time()
		source_ended = True
		if registry.open_conversation_id == source_id:
			registry.open_conversation_id = None
	if backend is not None:
		_spawn_bg(
			backend.remove_conversation_member(source_id, old_key),
			label=f"fb_remove_member:{source_id}:{old_key}",
		)
		_spawn_bg(
			backend.write_conversation_member(target_id, caller_member),
			label=f"fb_write_member:{target_id}:{actual_sender}",
		)
		if source_ended:
			_spawn_bg(
				backend.set_conversation_state(source_id, "ended"),
				label=f"fb_set_state:{source_id}:ended",
			)


async def _queue_for_intro(
	registry: Registry,
	conversation_id: str,
	cli_session_id: str,
	sender: str,
	cwd: str,
	timeout_seconds: float,
) -> str:
	"""Append a QueueEntry(waiting_kind='enter') for the caller; await the wake
	payload. Returns the payload string.

	Caller is assumed to already be a member of conversation_id (caller of
	enter_conversation may have just been migrated/added)."""
	conv = registry.conversations[conversation_id]
	# Locate caller's member
	caller_member = None
	for m in conv.members_active.values():
		if m.cli_session_id == cli_session_id:
			caller_member = m
			break
	if caller_member is None:
		return "ERROR: enter_conversation: caller not a member of target conversation."
	future = asyncio.get_event_loop().create_future()
	entry = {
		"member": caller_member,
		"future": future,
		"waiting_kind": "enter",
		"block_position": time.monotonic(),
	}
	conv.wait_queue.append(entry)
	try:
		result = await asyncio.wait_for(future, timeout=timeout_seconds)
		return result
	except asyncio.TimeoutError:
		try:
			conv.wait_queue.remove(entry)
		except ValueError:
			pass
		return "__TIMEOUT__"
	except asyncio.CancelledError:
		try:
			conv.wait_queue.remove(entry)
		except ValueError:
			pass
		raise


async def _inject_combine_intro(registry: Registry, target: Conversation, sender: str, backend=None) -> None:
	"""Inject a 'you've been moved' intro into target conversation log so the
	moved member sees context on their next call.

	For now, this just appends a system message to target.messages. Task 31's
	dispatcher will eventually surface this via an inject-queue mechanism.
	backend: optional ConversationStore — if provided, a Firebase message write is issued.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	msg = {
		"seq": len(target.messages),
		"sender": "<system>",
		"type": "system",
		"text": f"{sender} joined via combine. Call enter_conversation(sender='{sender}') to receive the conversation history.",
		"timestamp": _now_iso(),
	}
	target.messages.append(msg)
	if backend is not None:
		_spawn_bg(
			backend.write_conversation_message(target.id, msg),
			label=f"fb_combine_intro:{target.id}:{sender}",
		)


async def _spawn_pending_for_combine_resume(
	pending_dir,
	member: ConversationMember,
	target_id: str,
	source_id: str,
) -> None:
	"""Write a spawn-pending JSON file the launcher script will pick up to fire
	`claude --resume <session_id>` for a dormant member during combine.

	pending_dir: a pathlib.Path to the directory where spawn-pending files are written.
	The launcher (scripts/spawn-launcher.ps1) atomically claims and processes these files.
	"""
	import json
	import uuid
	from pathlib import Path as _Path
	if pending_dir is None:
		# Test mode / no spawn root configured; skip without raising
		return
	spawn_id = uuid.uuid4().hex
	pending = {
		"type": "combine_resume",
		"target_conversation_id": target_id,
		"source_conversation_id": source_id,
		"agents": [{
			"surface": member.surface,
			"cli_session_id": member.cli_session_id,
			"prompt": f"You were moved from conversation '{source_id}' to '{target_id}' via combine. Call enter_conversation(sender='{member.sender}') to receive the new conversation's history.",
			"project_path": member.cwd,
			"prior_sender": member.sender,
		}],
	}
	pending_path = _Path(pending_dir) / f"spawn-pending-{spawn_id}.json"
	pending_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")


async def _perform_combine(
	registry: Registry,
	source_id: str,
	target_id: str,
	logger,
	pending_dir,
	backend=None,
) -> str:
	"""Core combine logic: move movable members of source into target, end source.

	Returns an "ok. combined ..." string on success or an "ERROR: ..." string on
	validation failure. Does not acquire any external locks — callers that need
	lock ordering (e.g., the MCP tool handler) should obtain conv.lock before
	calling.

	logger: object with a surface_error(msg) coroutine (or None to skip logging).
	pending_dir: pathlib.Path for spawn-pending files, or None (skips file writes).
	backend: optional ConversationStore — if provided, Firebase writes are issued.
	"""
	from server.gateway.bg_tasks import _spawn_bg
	if source_id == target_id:
		return "ERROR: source and target must differ"
	source = registry.conversations.get(source_id)
	target = registry.conversations.get(target_id)
	if not source or source.state != "active":
		return f"ERROR: source conversation {source_id} not Active"
	if not target or target.state != "active":
		return f"ERROR: target conversation {target_id} not Active"
	if not any(not m.session_lost_permanently for m in source.members_active.values()):
		return "ERROR: source has no movable members"

	# Lock ordering: smaller id first to avoid AB-BA deadlock
	locks = sorted([source, target], key=lambda c: c.id)
	async with locks[0].lock, locks[1].lock:
		moved_names = []
		removed_from_source: list[str] = []
		for sender_key, member in list(source.members_active.items()):
			if member.session_lost_permanently:
				continue  # stay in source for visibility
			# Disambiguate before inserting into target to avoid clobbering existing members
			actual_sender = _disambiguate_sender(target, member.sender)
			member.sender = actual_sender
			if member.alive:
				registry.bind_session(member.cli_session_id, target_id)
				member.last_seen_seq = 0
				del source.members_active[sender_key]
				target.members_active[actual_sender] = member
				await _inject_combine_intro(registry, target, actual_sender, backend=backend)
				moved_names.append(actual_sender)
				removed_from_source.append(sender_key)
				if backend is not None:
					_spawn_bg(
						backend.remove_conversation_member(source_id, sender_key),
						label=f"fb_remove_member:{source_id}:{sender_key}",
					)
					_spawn_bg(
						backend.write_conversation_member(target_id, member),
						label=f"fb_write_member:{target_id}:{actual_sender}",
					)
			else:
				registry.bind_session(member.cli_session_id, target_id)
				await _spawn_pending_for_combine_resume(pending_dir, member, target_id, source_id)
				member.last_seen_seq = 0
				del source.members_active[sender_key]
				target.members_active[actual_sender] = member
				moved_names.append(actual_sender)
				removed_from_source.append(sender_key)
				if backend is not None:
					_spawn_bg(
						backend.remove_conversation_member(source_id, sender_key),
						label=f"fb_remove_member:{source_id}:{sender_key}",
					)
					_spawn_bg(
						backend.write_conversation_member(target_id, member),
						label=f"fb_write_member:{target_id}:{actual_sender}",
					)
		now_ts = time.time()
		target_msg = {
			"seq": len(target.messages),
			"sender": "<system>",
			"type": "system",
			"text": f"Merged with '{source.title}'. New members: {', '.join(moved_names) or '(none)'}",
			"timestamp": _now_iso(),
		}
		target.messages.append(target_msg)
		target.last_activity_at = now_ts
		source_msg = {
			"seq": len(source.messages),
			"sender": "<system>",
			"type": "system",
			"text": f"Merged into '{target.title}'",
			"timestamp": _now_iso(),
		}
		source.messages.append(source_msg)
		# Migrate waiters from source.wait_queue. Members were updated in-place
		# above; their wait_entries still hold valid refs. Entries for migrated
		# members move to target.wait_queue (where _wake_one_from(target) below
		# can deliver the merge marker via FIFO). Entries for members that
		# stayed in source (session_lost_permanently) are drained with a
		# sentinel since source is about to end and would otherwise strand
		# their futures for 24h.
		for entry in source.wait_queue:
			member_ref = entry.get("member")
			if (
				member_ref is not None
				and member_ref.sender in target.members_active
				and target.members_active[member_ref.sender] is member_ref
			):
				target.wait_queue.append(entry)
			else:
				fut = entry.get("future")
				if fut is not None and not fut.done():
					fut.set_result("__CONVERSATION_ENDED__\n(merged into target)")
		source.wait_queue.clear()
		source.state = "ended"
		source.ended_at = time.time()
		open_cleared = False
		if registry.open_conversation_id == source_id:
			registry.open_conversation_id = None
			open_cleared = True
		if backend is not None:
			_spawn_bg(
				backend.write_conversation_message(target_id, target_msg),
				label=f"fb_write_combine_msg_target:{target_id}",
			)
			_spawn_bg(
				backend.write_conversation_message(source_id, source_msg),
				label=f"fb_write_combine_msg_source:{source_id}",
			)
			_spawn_bg(
				backend.set_conversation_state(source_id, "ended"),
				label=f"fb_set_state:{source_id}:ended",
			)
			_spawn_bg(
				backend.set_conversation_last_activity(target_id, now_ts),
				label=f"fb_last_activity:{target_id}",
			)
			if open_cleared:
				_spawn_bg(
					backend.set_open_conversation_id(None),
					label=f"fb_clear_open_id:{source_id}",
				)
	# Wake one waiter in target so any blocked agent sees the merge marker
	async with target.lock:
		_wake_one_from(target)
	return f"ok. combined {source_id} into {target_id} ({len(moved_names)} member(s))"


def _now_iso() -> str:
	from datetime import datetime, timezone
	return datetime.now(timezone.utc).isoformat()


def _wake_one_from(conversation: Conversation) -> bool:
	"""Pop the FIFO-oldest entry from conv.wait_queue, resolve its future with the
	appropriate wake payload. Returns True if a wake occurred, False if queue was empty."""
	if not conversation.wait_queue:
		return False
	entry = conversation.wait_queue.popleft()
	future = entry["future"]
	member = entry["member"]
	kind = entry["waiting_kind"]
	if future.done():
		return False  # already resolved (race); skip
	payload = _compose_wake_payload(conversation, member, kind)
	future.set_result(payload)
	# Update last_seen_seq so the next wake doesn't re-deliver
	member.last_seen_seq = len(conversation.messages)
	return True
