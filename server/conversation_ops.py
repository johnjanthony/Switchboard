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
	# Wake any mint-path opener blocked on conv.open_peer_future. The opener's
	# wake payload identifies the newly-joined peer by sender so they can greet
	# by name. Newline-separated so the leading line still matches the legacy
	# `ok. open_conversation = <id>` format that existing parsers split on.
	fut = conv.open_peer_future
	if fut is not None and not fut.done():
		fut.set_result(
			f"ok. open_conversation = {conversation_id}\n"
			f"Peer '{actual_sender}' joined."
		)
		conv.open_peer_future = None


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
		if registry.open_conversation_id == source_id:
			registry.open_conversation_id = None
	if backend is not None:
		_spawn_bg(
			backend.remove_conversation_member(source_id, old_sender),
			label=f"fb_remove_member:{source_id}:{old_sender}",
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
	# Wake any opener blocked on the target's open_peer_future. Migration is a
	# peer-join from the target's POV, so the lobby/bootstrap wake protocol
	# applies the same way as _add_member.
	fut = target.open_peer_future
	if fut is not None and not fut.done():
		fut.set_result(
			f"ok. open_conversation = {target_id}\n"
			f"Peer '{actual_sender}' joined."
		)
		target.open_peer_future = None


async def _queue_for_open_peer(
	registry: Registry,
	conversation_id: str,
	cli_session_id: str,
	timeout_seconds: float,
	backend=None,
	end_conv_on_timeout: bool = True,
) -> str:
	"""Block the caller on conv.open_peer_future until a peer becomes an alive
	member (which resolves the future from inside _add_member), until force-end
	or session-end resolves the future, or until timeout.

	Two callers:
	- open_conversation mint path: end_conv_on_timeout=True. A timeout means
	  no one joined the just-minted room, so we force-end it to avoid leaking
	  an orphan.
	- message_and_await_agent sole-alive + open-marker (lobby-hold): end_conv_on_timeout=False.
	  The conv was already established; a timeout just means "no peer arrived
	  this round." Return __TIMEOUT__ but leave the conv alive so the caller
	  can poll again or explicitly leave_conversation."""
	conv = registry.conversations[conversation_id]
	future = asyncio.get_event_loop().create_future()
	conv.open_peer_future = future
	try:
		return await asyncio.wait_for(future, timeout=timeout_seconds)
	except asyncio.TimeoutError:
		if conv.open_peer_future is future:
			conv.open_peer_future = None
		if end_conv_on_timeout:
			from server.gateway.dispatch import handle_force_end
			await handle_force_end(registry, conversation_id, backend=backend)
		return "__TIMEOUT__"
	except asyncio.CancelledError:
		if conv.open_peer_future is future:
			conv.open_peer_future = None
		raise


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
	caller_member = conv.members_active.get(cli_session_id)
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
				# Wake any opener blocked on the target's open_peer_future:
				# combine is a peer-join from the target's POV, exactly like
				# _migrate_member's resolution (H01: without this, a
				# lobby-holding sole member never learns a peer arrived).
				fut = target.open_peer_future
				if fut is not None and not fut.done():
					fut.set_result(
						f"ok. open_conversation = {target_id}\n"
						f"Peer '{actual_sender}' joined."
					)
					target.open_peer_future = None
				moved_names.append(actual_sender)
				removed_from_source.append(old_sender)
				if backend is not None:
					_spawn_bg(
						backend.remove_conversation_member(source_id, old_sender),
						label=f"fb_remove_member:{source_id}:{old_sender}",
					)
					_spawn_bg(
						backend.write_conversation_member(target_id, member),
						label=f"fb_write_member:{target_id}:{actual_sender}",
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
						backend.remove_conversation_member(source_id, old_sender),
						label=f"fb_remove_member:{source_id}:{old_sender}",
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
