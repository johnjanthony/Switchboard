"""Hydrate server in-memory state from Firebase on startup.

Reads the persistent schema written by the new conversations model (parent design
2026-05-19) and rebuilds Registry state so a server restart doesn't wipe live
conversations.

Skips Ended conversations — they're archived in Firebase but don't need to live
in memory. Their continued_from chains are still readable from Firebase if any
future flow needs them.

Doesn't rehydrate: wait_queue (futures die with the process), pending_responses
(same), conv.lock (new lock per startup is fine). Any agent blocked on a future
at restart time will eventually time out or surface CancelledError on their next
MCP call — acceptable degradation per the parent design's T-001 acknowledgment.
The SessionRegistry roster (sessions/) IS rehydrated when session_registry is
passed in, including terminal (ended/lost) records — the sweeper needs those in
memory to retention-prune their RTDB entries.

Parked pendings (T-001): /conversations/<id>/pending_questions records that
carry cliSessionId + askedAt ARE rehydrated - as future-less PendingRequests
in Registry._pending - so answers arriving after a restart still resolve.
Records missing those fields (written by a pre-parking server) are cancelled
once at hydration. The answered_question_msg_ids subtree was retired
(F-66/F-73): the phone derives answered-state from message flags, so the
write had no reader.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from firebase_admin import db

from server.registry import (
	Conversation,
	ConversationMember,
	Registry,
)

# The message types live code appends to conv.messages (cli_session_end,
# conversation_ops, dispatch, handlers, spawn). Questions, answers, notifies
# and documents are persisted for the phone but never enter the in-memory
# log - hydration must not reload them, or len(conv.messages), wake deltas
# and last_seen_seq cursors change meaning across a restart (DT-4).
_LIVE_MESSAGE_TYPES = {"system", "agent_msg", "parting"}


async def hydrate_from_firebase(registry: Registry, backend, logger, session_registry=None) -> None:
	"""Restore registry state from Firebase. Called once at server startup,
	BEFORE listeners spawn and BEFORE the FastMCP app starts serving.
	"""
	# 1. Global settings
	try:
		away_mode = await _read_path("global_settings/away_mode")
		if isinstance(away_mode, bool):
			registry._global_away = away_mode
	except Exception as exc:
		await logger.surface_error(f"hydration_global_away_failed: {exc}")

	# 2. Conversations
	conversations_data = None
	try:
		conversations_data = await _read_path("conversations")
		if isinstance(conversations_data, dict):
			for conv_id, conv_node in conversations_data.items():
				try:
					_hydrate_conversation(registry, conv_id, conv_node)
				except Exception as exc:
					await logger.surface_error(
						f"hydration_conversation_failed: conv_id={conv_id} {exc}"
					)
				conv = registry.conversations.get(conv_id)
				if conv is None:
					continue
				# Messages live in the top-level /messages/<conv_id> node
				# (WP-10). Read per hydrated Active conversation; reload only
				# the live-log subset (DT-4), ordered by push key.
				try:
					messages_node = await _read_path(f"messages/{conv_id}")
				except Exception as exc:
					await logger.surface_error(
						f"hydration_messages_read_failed: conv_id={conv_id} {exc}"
					)
					continue
				if isinstance(messages_node, dict):
					conv.messages.extend(
						v for _k, v in sorted(messages_node.items())
						if isinstance(v, dict) and v.get("type") in _LIVE_MESSAGE_TYPES
					)
	except Exception as exc:
		await logger.surface_error(f"hydration_conversations_read_failed: {exc}")

	# 2b. Parked pendings (T-001): rebuild each surviving pending_questions
	# record as a future-less PendingRequest so an answer arriving after the
	# restart still resolves. Records written by a pre-parking server (no
	# cliSessionId/askedAt), records under conversations that did not hydrate
	# (ended/degenerate), and stray cancelled records are cancelled once - the
	# old startup-sweep behavior, applied one final time.
	parked = legacy_cancelled = 0
	if isinstance(conversations_data, dict):
		for conv_id, conv_node in conversations_data.items():
			if not isinstance(conv_node, dict):
				continue
			pending_node = conv_node.get("pending_questions")
			if not isinstance(pending_node, dict):
				continue
			for request_id, rec in pending_node.items():
				if not isinstance(rec, dict):
					continue
				cli_session_id = rec.get("cliSessionId")
				asked_at = _parse_iso(rec.get("askedAt"))
				parkable = (
					conv_id in registry.conversations
					and isinstance(cli_session_id, str) and cli_session_id
					and asked_at is not None
					and not rec.get("cancelled")
				)
				if parkable:
					registry.add_parked(
						conv_id, cli_session_id,
						sender=rec.get("sender") or "Agent",
						request_id=request_id,
						msg_id=rec.get("msgId"),
						question=rec.get("questionText"),
						started_at=asked_at,
					)
					parked += 1
				else:
					if backend is not None:
						try:
							await backend.mark_question_cancelled(conv_id, request_id)
						except Exception as exc:
							await logger.surface_error(
								f"hydration_pending_cancel_failed: conv_id={conv_id} request_id={request_id} {exc}"
							)
					legacy_cancelled += 1
	if parked or legacy_cancelled:
		await logger.info(f"hydration_parked_pendings: parked={parked} legacy_cancelled={legacy_cancelled}")

	# 2c. Session roster. Terminal records hydrate too: the sweeper can only
	# retention-prune RTDB entries it holds in memory.
	if session_registry is not None:
		try:
			sessions_node = await _read_path("sessions")
			if isinstance(sessions_node, dict):
				for _sid, data in sessions_node.items():
					if isinstance(data, dict):
						session_registry.hydrate_record(data)
		except Exception as exc:
			await logger.surface_error(f"hydration_sessions_failed: {exc}")

	# 3. Session home pointers.
	# Skip pointers that reference a conversation that wasn't hydrated (i.e.
	# the home conv is Ended or has been deleted). Re-binding a session to a
	# now-Ended home would defeat the dormant-fallback home-pointer cleanup
	# performed during force-end and re-introduce the stale-pointer bug.
	try:
		sessions_data = await _read_path("cli_sessions")
		if isinstance(sessions_data, dict):
			for session_id, session_node in sessions_data.items():
				if not isinstance(session_node, dict):
					continue
				home_id = session_node.get("home_conversation_id")
				if not isinstance(home_id, str) or not home_id:
					continue
				if home_id not in registry.conversations:
					continue
				registry._session_home_conversation_id[session_id] = home_id
	except Exception as exc:
		await logger.surface_error(f"hydration_cli_sessions_failed: {exc}")

	# 3b. Reconcile duplicate alive members (REV-104): a crash between the
	# halves of a pre-atomic member move could leave the same cli_session_id
	# alive in two Active conversations, and the binding derivation below
	# would resolve it silently by iteration order. Keep the copy with the
	# later joined_at (exact ties: larger conv_id string - deterministic,
	# never restart-order); demote every other copy to that conversation's
	# members_history, mirror the demotion to Firebase, and say so loudly.
	sightings: dict[str, list] = {}
	for conv_id, conv in registry.conversations.items():
		if conv.state != "active":
			continue
		for member in conv.members_active.values():
			if member.cli_session_id and member.alive:
				sightings.setdefault(member.cli_session_id, []).append((conv_id, member))
	for dup_session_id, entries in sightings.items():
		if len(entries) < 2:
			continue
		entries.sort(key=lambda e: (e[1].joined_at, e[0]))
		winner_conv_id = entries[-1][0]
		for conv_id, member in entries[:-1]:
			loser_conv = registry.conversations[conv_id]
			del loser_conv.members_active[dup_session_id]
			member.alive = False
			loser_conv.members_history.append(member)
			if backend is not None:
				try:
					await backend.remove_conversation_member(conv_id, member.sender)
					await backend.write_conversation_member_history(conv_id, member)
				except Exception as exc:
					await logger.surface_error(f"hydration_duplicate_member_mirror_failed: {exc}")
			await logger.surface_error(
				f"hydration_duplicate_member_demoted: session {dup_session_id} alive in "
				f"{conv_id} and {winner_conv_id}; kept {winner_conv_id}"
			)

	# 4. Derive session_to_conversation_id from alive members in hydrated
	# Active conversations ONLY. Dormant members stay unbound: the
	# steady-state invariant is "dormant = unbound" (cli_session_end clears
	# the binding when a CLI dies), and both resume eligibility (spawn.py)
	# and apply_fallback's dormant short-circuit (session_fallback.py) rely
	# on it. Resume re-binds (and flips alive) when it actually relaunches a
	# member. Re-binding dormant members here used to break phone Resume
	# permanently after a restart (H03/M21).
	for conv_id, conv in registry.conversations.items():
		if conv.state != "active":
			continue
		for member in conv.members_active.values():
			if not member.cli_session_id:
				continue
			if member.alive:
				registry.bind_session(member.cli_session_id, conv_id)

	await logger.info(
		f"hydration_complete: conversations={len(registry.conversations)} "
		f"sessions_bound={len(registry._session_to_conversation_id)} "
		f"away={registry._global_away}"
	)


async def _read_path(path: str) -> Any:
	"""Read a Firebase path. Returns whatever Firebase returned, or None if missing."""
	def _get():
		return db.reference(path).get()
	return await asyncio.to_thread(_get)


def _hydrate_conversation(registry: Registry, conv_id: str, conv_node: Any) -> None:
	"""Reconstruct a single Conversation. Skip Ended ones."""
	if not isinstance(conv_node, dict):
		return
	meta = conv_node.get("meta")
	if not isinstance(meta, dict) or not meta:
		return  # skip degenerate: missing or empty meta
	state = meta.get("state", "active")
	if state != "active":
		return  # skip Ended

	# Reconstruct Conversation dataclass
	conv = Conversation(
		id=conv_id,
		title=meta.get("title", conv_id),
		state="active",
		continued_from=meta.get("continued_from"),
		origin=meta.get("origin"),
		created_at=_as_float(meta.get("created_at"), 0.0),
		last_activity_at=_as_float(meta.get("last_activity_at"), 0.0),
		ended_at=_as_float(meta.get("ended_at"), None),
		hidden=bool(meta.get("hidden", False)),
	)

	# Members
	members_node = conv_node.get("members_active") or {}
	if isinstance(members_node, dict):
		for sender, member_data in members_node.items():
			if not isinstance(member_data, dict):
				continue
			cli_session_id = member_data.get("cli_session_id")
			if not isinstance(cli_session_id, str) or not cli_session_id:
				continue
			member = ConversationMember(
				cli_session_id=cli_session_id,
				sender=member_data.get("sender", sender),
				cwd=member_data.get("cwd", ""),
				surface=member_data.get("surface", "windows"),
				joined_at=_as_float(member_data.get("joined_at"), 0.0),
				alive=bool(member_data.get("alive", True)),
				session_lost_permanently=bool(member_data.get("session_lost_permanently", False)),
				session_ended_at=member_data.get("session_ended_at"),
				session_end_reason=member_data.get("session_end_reason"),
				left_at=_as_float(member_data.get("left_at"), None),
				last_seen_seq=int(member_data.get("last_seen_seq", 0) or 0),
			)
			conv.members_active[member.cli_session_id] = member

	# Departed members (parting metadata) — restored from /conversations/<id>/members_history
	history_node = conv_node.get("members_history") or {}
	if isinstance(history_node, dict):
		for sender, member_data in history_node.items():
			if not isinstance(member_data, dict):
				continue
			cli_session_id = member_data.get("cli_session_id")
			if not isinstance(cli_session_id, str) or not cli_session_id:
				continue
			departed = ConversationMember(
				cli_session_id=cli_session_id,
				sender=member_data.get("sender", sender),
				cwd=member_data.get("cwd", ""),
				surface=member_data.get("surface", "windows"),
				joined_at=_as_float(member_data.get("joined_at"), 0.0),
				alive=bool(member_data.get("alive", False)),
				session_lost_permanently=bool(member_data.get("session_lost_permanently", False)),
				session_ended_at=member_data.get("session_ended_at"),
				session_end_reason=member_data.get("session_end_reason"),
				left_at=_as_float(member_data.get("left_at"), None),
				last_seen_seq=int(member_data.get("last_seen_seq", 0) or 0),
			)
			conv.members_history.append(departed)

	registry.conversations[conv_id] = conv


def _as_float(value, default):
	if value is None:
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _parse_iso(value) -> datetime | None:
	if not isinstance(value, str) or not value:
		return None
	try:
		return datetime.fromisoformat(value)
	except ValueError:
		return None
