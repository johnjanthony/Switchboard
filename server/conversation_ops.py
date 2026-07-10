"""Conversation-mutation helpers used by MCP tool handlers and spawn flows.

Helpers in this module mutate Registry state safely (single-event-loop access
assumption from registry.py applies). They are the primary internal API used
by tool handlers in server/gateway/handlers.py to materialize ConversationMember
entries, route sessions, and perform combine/migrate/queue operations.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import TYPE_CHECKING

from server.registry import Conversation, ConversationMember, Registry


_WSL_MOUNT_RE = re.compile(r"^/mnt/[a-z]/", re.IGNORECASE)

JOIN_CANDIDATE_WINDOW_SECONDS = 1800.0


def _disambiguate_sender(conv, desired: str, exclude_session_id: str | None = None) -> str:
	"""Sender is a display label; make it unique among the conversation's members
	(excluding the caller's own entry when renaming) by appending ' 2', ' 3', ...
	Identity is the members_active key (cli_session_id), never the sender."""
	taken = {
		m.sender for sid, m in conv.members_active.items()
		if sid != exclude_session_id
	}
	if desired not in taken:
		return desired
	n = 2
	while f"{desired} {n}" in taken:
		n += 1
	return f"{desired} {n}"


def _infer_surface(cwd: str) -> str:
	"""Returns 'wsl' for /mnt/<letter>/... or /home/... cwds; 'windows' otherwise."""
	if _WSL_MOUNT_RE.match(cwd):
		return "wsl"
	if cwd.startswith("/home/"):
		return "wsl"
	return "windows"


def _find_join_candidate(registry, now_ts: float):
	"""The marker's replacement: a ref-less joiner lands with a fresh, still-solo
	ref-less mint - and ONLY when that target is unambiguous. Zero or several
	candidates both mean 'mint a new room'."""
	candidates = []
	for conv in registry.conversations.values():
		if conv.state != "active" or conv.origin != "join":
			continue
		alive = [m for m in conv.members_active.values() if m.alive]
		if len(alive) != 1:
			continue
		if (conv.created_at or 0.0) < now_ts - JOIN_CANDIDATE_WINDOW_SECONDS:
			continue
		candidates.append(conv)
	return candidates[0] if len(candidates) == 1 else None


async def _create_active_conversation_for(
	registry: Registry,
	cli_session_id: str,
	cwd: str,
	sender: str,
	backend=None,
	title: str | None = None,
	origin: str = "fallback",
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
		return await _create_active_conversation_for_locked(
			registry, cli_session_id, cwd, sender, backend, title, origin,
		)


async def _create_active_conversation_for_locked(
	registry: Registry,
	cli_session_id: str,
	cwd: str,
	sender: str,
	backend=None,
	title: str | None = None,
	origin: str = "fallback",
) -> str:
	"""Inner implementation called while holding session_create_lock. Do not call directly."""
	from server.gateway.bg_tasks import _spawn_bg
	conv_id = "conv-" + uuid.uuid4().hex
	resolved_title = title if title else f"{sender} · {cwd}"
	conv = Conversation(id=conv_id, title=resolved_title, origin=origin)
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
	conv.members_active[cli_session_id] = member
	registry.conversations[conv_id] = conv
	registry.bind_session(cli_session_id, conv_id)
	if registry.sessions is not None:
		registry.sessions.set_sender(cli_session_id, member.sender)
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
				origin=origin,
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


async def _resolve_conversation_and_member(
	registry: Registry,
	cli_session_id: str,
	cwd: str,
	sender: str,
	backend=None,
	mint_if_unbound: bool = True,
) -> str | None:
	"""Return the conversation id the session belongs to, guaranteeing the
	caller is a member of it.

	- Truly unbound (no binding): if mint_if_unbound, mint a fresh single-agent
	  Active conversation (which already creates the member); else return None
	  so the caller applies its own unbound policy (message_and_await_agent
	  errors rather than minting into an empty room).
	- Bound to a conv_id whose Conversation object is not loaded: return the id
	  unchanged. Do not mint or relocate the session; membership cannot be
	  ensured without a conversation object, and minting here would silently
	  move the session and break legacy routing.
	- Bound with the Conversation present but no member for this cli_session_id
	  (the fresh-spawn state: handle_fresh binds without adding a member): add
	  the member.
	- Bound with a member already present: no-op.
	"""
	conv_id = registry.session_to_conversation_id.get(cli_session_id)
	if conv_id is None:
		if not mint_if_unbound:
			return None
		return await _create_active_conversation_for(
			registry, cli_session_id, cwd, sender, backend=backend,
		)
	conv = registry.conversations.get(conv_id)
	if conv is None:
		return conv_id
	if cli_session_id not in conv.members_active:
		async with conv.lock:
			# Re-check inside the lock: a concurrent first call from the same
			# session may have added the member already.
			if cli_session_id not in conv.members_active:
				await _add_member(registry, conv_id, cli_session_id, sender, cwd, backend=backend)
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
	conv.members_active[cli_session_id] = member
	registry.bind_session(cli_session_id, conversation_id)
	if registry.sessions is not None:
		registry.sessions.set_sender(cli_session_id, member.sender)
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
	caller_member = source.members_active.get(cli_session_id)
	if caller_member is None:
		# Not in source — fall through to _add_member behavior
		await _add_member(registry, target_id, cli_session_id, sender, cwd, backend=backend)
		return
	old_sender = caller_member.sender
	del source.members_active[cli_session_id]
	actual_sender = _disambiguate_sender(target, sender)
	caller_member.sender = actual_sender
	caller_member.cwd = cwd
	caller_member.surface = _infer_surface(cwd)
	caller_member.last_seen_seq = 0
	target.members_active[cli_session_id] = caller_member
	registry.bind_session(cli_session_id, target_id)
	if registry.sessions is not None:
		registry.sessions.set_sender(cli_session_id, caller_member.sender)
	# If source has no alive members AND no dormant members → end source
	source_ended = False
	if not source.members_active:
		source.state = "ended"
		source.ended_at = time.time()
		source_ended = True
	if backend is not None:
		_spawn_bg(
			backend.move_conversation_member(source_id, target_id, caller_member, old_sender, end_source=source_ended),
			label=f"fb_move_member:{source_id}->{target_id}:{actual_sender}",
		)


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
		"text": f"{sender} joined via combine. Call join_conversation(sender='{sender}') to collect the conversation history.",
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
			"prompt": f"You were moved from conversation '{source_id}' to '{target_id}' via combine. Call join_conversation(sender='{member.sender}', ref='{target_id}') to collect the new conversation's history.",
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

	# Dormant movable members mean a relaunch (`claude --resume` via the
	# scheduled task), which requires a desktop session: the same gate as
	# handle_fresh/handle_resume (P0-4). Abort the whole combine rather than
	# stranding a half-moved roster. pending_dir=None (test mode) writes no
	# pending files, so no gate applies.
	movable_dormant = [
		m for m in source.members_active.values()
		if not m.session_lost_permanently and not m.alive
	]
	if movable_dormant and pending_dir is not None:
		from server import spawn as _spawn_mod
		if not await _spawn_mod.user_has_interactive_session():
			names = ", ".join(m.sender for m in movable_dormant)
			return (
				f"ERROR: combine aborted: dormant member(s) {names} need a relaunch "
				"but no one is logged in to the desktop. Sign in (locally or via RDP) and try again."
			)

	# Lock ordering: smaller id first to avoid AB-BA deadlock
	locks = sorted([source, target], key=lambda c: c.id)
	combine_resume_count = 0
	async with locks[0].lock, locks[1].lock:
		moved_names = []
		removed_from_source: list[str] = []
		for sid, member in list(source.members_active.items()):
			if member.session_lost_permanently:
				continue  # stay in source for visibility
			old_sender = member.sender
			# Disambiguate before inserting into target to avoid clobbering existing members
			actual_sender = _disambiguate_sender(target, member.sender)
			member.sender = actual_sender
			if registry.sessions is not None:
				registry.sessions.set_sender(member.cli_session_id, actual_sender)
			if member.alive:
				registry.bind_session(member.cli_session_id, target_id)
				member.last_seen_seq = 0
				del source.members_active[sid]
				target.members_active[sid] = member
				await _inject_combine_intro(registry, target, actual_sender, backend=backend)
				moved_names.append(actual_sender)
				removed_from_source.append(old_sender)
				if backend is not None:
					_spawn_bg(
						backend.move_conversation_member(source_id, target_id, member, old_sender, end_source=False),
						label=f"fb_move_member:{source_id}->{target_id}:{actual_sender}",
					)
			else:
				# Dormant member: write the relaunch pending file, then bind
				# and flip alive TOGETHER (bound = alive-or-launching
				# invariant; handle_resume does the same). Clearing the
				# dormancy fields keeps message_and_await_agent's alive-peer
				# count honest while the relaunch is in flight. With
				# pending_dir=None (test mode) no relaunch happens, so the
				# member moves as-is and stays unbound.
				await _spawn_pending_for_combine_resume(pending_dir, member, target_id, source_id)
				if pending_dir is not None:
					member.alive = True
					member.session_ended_at = None
					member.session_end_reason = None
					member.left_at = None
					registry.bind_session(member.cli_session_id, target_id)
					combine_resume_count += 1
				member.last_seen_seq = 0
				del source.members_active[sid]
				target.members_active[sid] = member
				moved_names.append(actual_sender)
				removed_from_source.append(old_sender)
				if backend is not None:
					_spawn_bg(
						backend.move_conversation_member(source_id, target_id, member, old_sender, end_source=False),
						label=f"fb_move_member:{source_id}->{target_id}:{actual_sender}",
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
				and target.members_active.get(member_ref.cli_session_id) is member_ref
			):
				target.wait_queue.append(entry)
			else:
				fut = entry.get("future")
				if fut is not None and not fut.done():
					fut.set_result("__CONVERSATION_ENDED__\n(merged into target)")
		source.wait_queue.clear()
		source.state = "ended"
		source.ended_at = time.time()
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
	# Source is Ended: terminally end its pending ask_humans (REV-102). A live
	# asker - a member just migrated to target - wakes with a terminal envelope
	# telling it to re-ask; its binding already points at target, so the re-ask
	# lands there. A parked record is withdrawn on the phone. Re-keying the
	# pending to target was rejected: answers correlate by the answer's
	# conversation_id and the question message lives under source, so a phone
	# reply could never resolve a re-keyed record.
	from server.gateway.pending_lifecycle import terminate_pending
	for record in registry.pending_for_conversation(source_id):
		await terminate_pending(
			registry, backend, logger, record,
			resolve_text=f"__CONVERSATION_ENDED__\n(combined into {target_id}; re-ask your question there)",
			remember_resolved=True,
		)
	# Fire the launcher once if any dormant member got a combine_resume
	# pending file: the file alone does nothing until the scheduled task
	# runs (H08/H09: state used to say "resumed" with no process behind it).
	if combine_resume_count:
		from server import spawn as _spawn_mod
		try:
			await _spawn_mod.invoke_spawn_launcher(logger)
		except Exception as exc:
			# The combine itself already committed (members moved); only the
			# dormant-member relaunch failed. Surface it to the phone instead of
			# failing the combine or leaving members "alive" with no process (B4).
			# invoke_spawn_launcher already logged the failure.
			if backend is not None and hasattr(backend, "send_text"):
				try:
					await backend.send_text(
						f"Combined conversations, but relaunching dormant member(s) failed: {exc}. "
						"Resume them from the phone."
					)
				except Exception:
					pass
	# Wake one waiter in target so any blocked agent sees the merge marker
	async with target.lock:
		_wake_one_from(target)
	return f"ok. combined {source_id} into {target_id} ({len(moved_names)} member(s))"


def _now_iso() -> str:
	from datetime import datetime, timezone
	return datetime.now(timezone.utc).isoformat()


def _wake_one_from(conversation: Conversation) -> bool:
	"""Wake the FIFO-oldest LIVE waiter on conv.wait_queue: pop entries until one
	holds an unresolved future, resolve it with the appropriate wake payload, and
	advance that member's cursor. A popped entry whose future is already done is
	dead - its waiter's wait_for timed out or was cancelled before the waiter
	could reacquire conv.lock to dequeue itself (REV-101) - so it is discarded
	and the next waiter is tried; the dead waiter's own cleanup arm tolerates the
	entry being gone. Returns True if a wake occurred, False otherwise."""
	while conversation.wait_queue:
		entry = conversation.wait_queue.popleft()
		future = entry["future"]
		if future.done():
			continue
		member = entry["member"]
		kind = entry["waiting_kind"]
		payload = _compose_wake_payload(conversation, member, kind)
		future.set_result(payload)
		# Update last_seen_seq so the next wake doesn't re-deliver
		member.last_seen_seq = len(conversation.messages)
		return True
	return False


def _convene_notice(conversation_id: str, member_sender: str, peers: list) -> str:
	peer_str = ", ".join(peers) if peers else "(no peers yet)"
	return (
		f"John convened you into conversation {conversation_id} (peers: {peer_str}). "
		f"Call join_conversation(sender='{member_sender}', ref='{conversation_id}') to collect the "
		f"history (idempotent - you are already a member), then message_and_await_agent to speak."
	)


async def _wake_convened(registry, session_registry, conversation_id, woken_session_ids, logger, backend=None):
	"""Deliver the convene to each moved session by whatever structure it is
	actually blocked in RIGHT NOW (live derivation, not roster state): a blocked
	message_and_await future resolves immediately with a convened envelope; a
	pending ask_human gets the notice prepended to its eventual answer; everyone
	else gets a hook-delivered notice queued on their session record."""
	from server.gateway.handlers import _envelope
	target = registry.conversations.get(conversation_id)
	if target is None:
		return []
	woken_now: list[str] = []
	for sid in woken_session_ids:
		member = target.members_active.get(sid)
		if member is None:
			continue
		peers = [m.sender for k, m in target.members_active.items() if k != sid]
		log = _compose_wake_payload(target, member, "enter")
		envelope = _envelope("convened", conversation_id=conversation_id, peers=peers, log=log or None)
		notice = _convene_notice(conversation_id, member.sender, peers)

		# (b) Blocked in message_and_await, queued in some conversation's wait_queue.
		resolved = False
		for conv in list(registry.conversations.values()):
			for entry in list(conv.wait_queue):
				m = entry.get("member")
				if m is not None and m.cli_session_id == sid:
					fut = entry.get("future")
					try:
						conv.wait_queue.remove(entry)
					except ValueError:
						pass
					if fut is not None and not fut.done():
						fut.set_result(envelope)
						resolved = True
					break
			if resolved:
				break

		if resolved:
			member.last_seen_seq = len(target.messages)
			woken_now.append(sid)
			continue

		# (d) Blocked in ask_human: prepend the notice to the eventual human reply.
		pending_attached = False
		for pending in registry.all_pending():
			if pending.cli_session_id == sid:
				pending.notices.append(notice)
				pending_attached = True
				break
		if pending_attached:
			continue

		# (e) Otherwise: queue a hook-delivered notice on the session record.
		if session_registry is not None:
			session_registry.queue_notice(sid, notice)
	if logger is not None and woken_now:
		await logger.info(f"convene_woke_blocked: {woken_now}")
	return woken_now


def _convene_sender_for(registry, session_registry, cli_session_id: str) -> str:
	rec = session_registry.get(cli_session_id) if session_registry is not None else None
	if rec is not None and rec.sender:
		return rec.sender
	if rec is not None and rec.name:
		return rec.name
	return f"Agent {cli_session_id[:8]}"


async def _perform_convene(registry, session_registry, cmd: dict, logger, backend=None, spawn_handler=None) -> dict:
	from server.gateway.bg_tasks import _spawn_bg
	session_ids = [s for s in (cmd.get("session_ids") or []) if isinstance(s, str) and s]
	target = cmd.get("target") or "new"
	title = cmd.get("title") if isinstance(cmd.get("title"), str) else None

	result: dict = {"conversation_id": None, "convened": [], "skipped": [], "resuming": []}
	if not session_ids:
		return result

	# For an existing target, resolve upfront (it must exist and be Active). For
	# "new", mint LAZILY - only once a session actually routes in - so an
	# all-skipped convene leaves no orphan empty Active conversation.
	if target == "new":
		conv = None
		conv_id = None
	else:
		conv = registry.conversations.get(target)
		if conv is None or conv.state != "active":
			result["skipped"] = [{"session_id": s, "reason": "target not found or not Active"} for s in session_ids]
			if logger is not None:
				await logger.surface_error(f"convene_target_invalid: {target}")
			return result
		conv_id = target
		result["conversation_id"] = conv_id

	def _ensure_target():
		nonlocal conv, conv_id
		if conv is not None:
			return
		conv_id = "conv-" + uuid.uuid4().hex
		now = time.time()
		conv = Conversation(id=conv_id, title=title or f"Convened {len(session_ids)} agents", origin="convene")
		conv.created_at = now
		conv.last_activity_at = now
		registry.conversations[conv_id] = conv
		result["conversation_id"] = conv_id
		if backend is not None:
			_spawn_bg(
				backend.write_conversation_meta(
					conv_id, title=conv.title, state="active", continued_from=None,
					created_at=now, last_activity_at=now, ended_at=None, hidden=False,
					origin="convene",
				),
				label=f"fb_write_conv_meta:{conv_id}",
			)

	woken: list[str] = []
	for sid in session_ids:
		rec = session_registry.get(sid) if session_registry is not None else None
		if rec is None:
			result["skipped"].append({"session_id": sid, "reason": "not a live session"})
			continue
		if rec.state in ("ended", "lost"):
			if spawn_handler is None:
				result["skipped"].append({"session_id": sid, "reason": "resume unavailable"})
				continue
			if not rec.cwd:
				result["skipped"].append({"session_id": sid, "reason": "no cwd recorded"})
				continue
			_ensure_target()
			sender = _convene_sender_for(registry, session_registry, sid)
			prompt = (
				f"John convened you (by resume) into conversation {conv_id}. "
				"Tool calls auto-inject your cli_session_id. "
				f"Call join_conversation(sender='{sender}', ref='{conv_id}') to collect the history "
				"(idempotent - you are already a member), then message_and_await_agent to speak."
			)
			if session_registry is not None:
				session_registry.note_spawn_resume(sid, rec.cwd)
			ok = await spawn_handler.launch_resume_agent(
				session_id=sid, surface=rec.surface, cwd=rec.cwd, prompt=prompt, prior_sender=sender
			)
			if not ok:
				result["skipped"].append({"session_id": sid, "reason": "resume launch failed"})
				continue
			async with conv.lock:
				if sid not in conv.members_active:
					await _add_member(registry, conv_id, sid, sender, rec.cwd, backend=backend)
			result["resuming"].append(conv.members_active[sid].sender)
			continue
		sender = _convene_sender_for(registry, session_registry, sid)
		cwd = rec.cwd or ""
		bound_id = registry.session_to_conversation_id.get(sid)
		if conv_id is not None and bound_id == conv_id:
			member = conv.members_active.get(sid)
			if member is None:
				async with conv.lock:
					if sid not in conv.members_active:
						await _add_member(registry, conv_id, sid, sender, cwd, backend=backend)
			result["convened"].append(conv.members_active[sid].sender)
			woken.append(sid)
			continue
		if bound_id is None:
			_ensure_target()
			async with conv.lock:
				await _add_member(registry, conv_id, sid, sender, cwd, backend=backend)
			result["convened"].append(conv.members_active[sid].sender)
			woken.append(sid)
			continue
		source = registry.conversations.get(bound_id)
		if source is None:
			_ensure_target()
			async with conv.lock:
				await _add_member(registry, conv_id, sid, sender, cwd, backend=backend)
			result["convened"].append(conv.members_active[sid].sender)
			woken.append(sid)
			continue
		other_alive = [m for k, m in source.members_active.items() if k != sid and m.alive]
		if other_alive:
			result["skipped"].append({"session_id": sid, "reason": "in a multi-party conversation"})
			continue
		_ensure_target()
		locks = sorted([source, conv], key=lambda c: c.id)
		async with locks[0].lock, locks[1].lock:
			await _migrate_member(registry, bound_id, conv_id, sid, rec.sender or sender, cwd, backend=backend)
		result["convened"].append(conv.members_active[sid].sender)
		woken.append(sid)

	# Intro message: the transcript explains itself; also the documented backstop
	# for any lost wake notice.
	if result["convened"] or result["resuming"]:
		names = result["convened"]
		text = "John convened: " + ", ".join(names) if names else "John convened"
		if result["resuming"]:
			text += ("; " if names else ": ") + "resuming: " + ", ".join(result["resuming"])
		if result["skipped"]:
			skips = "; ".join(f"{s['session_id'][:8]} - {s['reason']}" for s in result["skipped"])
			text += f" (skipped: {skips})"
		msg = {
			"seq": len(conv.messages),
			"sender": "<system>",
			"type": "system",
			"text": text,
			"timestamp": _now_iso(),
		}
		conv.messages.append(msg)
		conv.last_activity_at = time.time()
		if backend is not None:
			_spawn_bg(backend.write_conversation_message(conv_id, msg), label=f"fb_convene_intro:{conv_id}")
			_spawn_bg(
				backend.set_conversation_last_activity(conv_id, conv.last_activity_at),
				label=f"fb_last_activity:{conv_id}",
			)

	await _wake_convened(registry, session_registry, conv_id, woken, logger, backend=backend)
	return result
