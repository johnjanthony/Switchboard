"""In-memory pending-request registry plus the Conversation routing maps.

All access happens on a single asyncio event loop, so no locking is required
on the registry dicts themselves; per-Conversation work is serialized via
each Conversation's own asyncio.Lock.

Pending requests are keyed by (conversation_id, sender) with supersede
semantics: if a new request arrives for the same (conversation_id, sender)
pair, the prior future is cancelled and replaced. Routing from a CLI session
to its current conversation uses session_to_conversation_id (hook-injected
cli_session_id → conv-<uuid>); cwd is informational only.
"""

from __future__ import annotations

import asyncio
import collections
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


@dataclass
class PendingRequest:
	conversation_id: str
	sender: str
	request_id: str
	future: asyncio.Future[str]
	started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	msg_id: str | None = None


@dataclass
class ConversationMember:
	cli_session_id: str                          # primary routing key
	sender: str                                  # display name, agent-supplied
	cwd: str                                     # informational; not used for routing
	surface: Literal["windows", "wsl"]
	joined_at: float
	alive: bool = True
	session_lost_permanently: bool = False
	session_ended_at: str | None = None
	session_end_reason: str | None = None
	left_at: float | None = None
	last_seen_seq: int = 0


@dataclass
class Conversation:
	id: str
	title: str
	state: Literal["active", "ended"] = "active"
	continued_from: str | None = None
	members_active: dict = None  # dict[sender, ConversationMember]
	members_history: list = None  # list[ConversationMember]
	messages: list = None
	pending_responses: dict = None
	wait_queue: collections.deque = None
	created_at: float = 0.0
	last_activity_at: float = 0.0
	ended_at: float | None = None
	hidden: bool = False
	lock: asyncio.Lock = None
	# Mint-path opener's await-peer promise: open_conversation blocks on this
	# future until a peer becomes an alive member (via _add_member) or until
	# the conv is torn down. Not hydrated — futures don't survive restart.
	open_peer_future: asyncio.Future | None = None

	def __post_init__(self):
		if self.members_active is None: self.members_active = {}
		if self.members_history is None: self.members_history = []
		if self.messages is None: self.messages = []
		if self.pending_responses is None: self.pending_responses = {}
		if self.wait_queue is None: self.wait_queue = collections.deque()
		if self.lock is None: self.lock = asyncio.Lock()


class Registry:
	def __init__(self) -> None:
		self._pending: dict[tuple[str, str], PendingRequest] = {}
		self.total_answered: int = 0
		self._global_away = False
		self._pending_mirror = None
		self._session_to_conversation_id: dict[str, str] = {}
		self._session_home_conversation_id: dict[str, str] = {}
		self._open_conversation_id: str | None = None
		self.conversations: dict[str, "Conversation"] = {}
		self._session_create_locks: dict[str, asyncio.Lock] = {}

	def session_create_lock(self, cli_session_id: str) -> asyncio.Lock:
		"""Returns (creating if needed) the per-session lock for the auto-create-on-first-call path.
		Guards against parallel tool calls (e.g. two concurrent ask_human invocations) both creating
		a new conversation for the same session."""
		lock = self._session_create_locks.get(cli_session_id)
		if lock is None:
			lock = asyncio.Lock()
			self._session_create_locks[cli_session_id] = lock
		return lock

	@property
	def session_to_conversation_id(self) -> dict[str, str]:
		return self._session_to_conversation_id

	@property
	def session_home_conversation_id(self) -> dict[str, str]:
		return self._session_home_conversation_id

	@property
	def open_conversation_id(self) -> str | None:
		return self._open_conversation_id

	@open_conversation_id.setter
	def open_conversation_id(self, value: str | None) -> None:
		self._open_conversation_id = value

	@property
	def global_away_mode(self) -> bool:
		return self._global_away

	@global_away_mode.setter
	def global_away_mode(self, value: bool) -> None:
		self._global_away = bool(value)

	def bind_session(self, session_id: str, conversation_id: str) -> None:
		self._session_to_conversation_id[session_id] = conversation_id

	def unbind_session(self, session_id: str) -> str | None:
		return self._session_to_conversation_id.pop(session_id, None)

	def set_session_home(self, session_id: str, conversation_id: str) -> None:
		self._session_home_conversation_id[session_id] = conversation_id

	@property
	def pending_count(self) -> int:
		return len(self._pending)

	@property
	def oldest_pending_age_seconds(self) -> float | None:
		if not self._pending:
			return None
		now = datetime.now(timezone.utc)
		oldest = min(r.started_at for r in self._pending.values())
		return (now - oldest).total_seconds()

	@property
	def active_conversations_count(self) -> int:
		return sum(1 for c in self.conversations.values() if c.state == "active")

	def add(
		self,
		conversation_id: str,
		sender: str,
		request_id: str,
		msg_id: str | None = None,
		return_superseded: bool = False,
	) -> asyncio.Future | tuple[asyncio.Future, str | None]:
		"""Add a pending request. If (conversation_id, sender) is already occupied,
		the prior PendingRequest is superseded: its future is cancelled, the entry
		removed, and the prior request_id returned (when return_superseded=True)
		so callers can mark the prior question's Firebase entry as cancelled.

		Returns the new Future. If return_superseded=True, returns
		(future, prior_request_id_or_None)."""
		key = (conversation_id, sender)
		prior_request_id = None
		existing = self._pending.pop(key, None)
		if existing is not None:
			prior_request_id = existing.request_id
			if not existing.future.done():
				existing.future.cancel()
			self._fire_pending_mirror(conversation_id, -1)
		future = asyncio.get_event_loop().create_future()
		self._pending[key] = PendingRequest(
			conversation_id=conversation_id,
			sender=sender,
			request_id=request_id,
			future=future,
			msg_id=msg_id,
		)
		self._fire_pending_mirror(conversation_id, +1)
		if return_superseded:
			return future, prior_request_id
		return future

	def get(self, key: tuple[str, str]) -> "PendingRequest | None":
		return self._pending.get(key)

	def resolve(self, conversation_id: str, sender: str, text: str, request_id: str | None = None) -> str | None:
		"""Resolve the pending request for (conversation_id, sender). Returns the
		request_id of the resolved entry, or None if no pending exists.

		If request_id is provided and does not match the occupying entry's
		request_id, this is a no-op (returns None, leaves the live entry intact):
		a stale or replayed answer for a superseded request must not resolve the
		newer entry that now holds the (conversation_id, sender) key (T-148)."""
		key = (conversation_id, sender)
		record = self._pending.get(key)
		if record is None:
			return None
		if request_id is not None and record.request_id != request_id:
			return None
		self._pending.pop(key, None)
		if not record.future.done():
			record.future.set_result(text)
		self._fire_pending_mirror(conversation_id, -1)
		return record.request_id

	def remove(self, conversation_id: str, sender: str, request_id: str | None = None) -> str | None:
		"""Remove the pending entry for (conversation_id, sender). Cancels the future if
		pending. Returns the request_id of the removed entry, or None.

		If request_id is provided and does not match the occupying entry's
		request_id, this is a no-op: a superseded asker's shielded cleanup must
		remove only its own entry, not the live entry that superseded it (T-148)."""
		key = (conversation_id, sender)
		record = self._pending.get(key)
		if record is None:
			return None
		if request_id is not None and record.request_id != request_id:
			return None
		self._pending.pop(key, None)
		if not record.future.done():
			record.future.cancel()
		self._fire_pending_mirror(conversation_id, -1)
		return record.request_id

	def all_pending(self) -> list["PendingRequest"]:
		"""Snapshot for bulk-respond on global exit (Slice I)."""
		return list(self._pending.values())

	def pending_for_conversation(self, conversation_id: str) -> list["PendingRequest"]:
		"""Snapshot of pending requests for a specific conversation."""
		return [p for p in self._pending.values() if p.conversation_id == conversation_id]

	def cancel_pending_for_conversation(self, conversation_id: str) -> list[str]:
		"""Pop and cancel every pending request for this conversation_id. Returns the
		list of request_ids that were cancelled so the caller can mark each
		question's Firebase entry cancelled (writing the WITHDRAWN marker).

		Used by spawn to clear stale pendings from a prior agent that died without
		surfacing CancelledError to its tool handler — the MCP streamable-HTTP
		transport doesn't reliably propagate client disconnects."""
		victims = [key for key, record in self._pending.items() if record.conversation_id == conversation_id]
		cancelled_request_ids: list[str] = []
		for key in victims:
			record = self._pending.pop(key)
			cancelled_request_ids.append(record.request_id)
			if not record.future.done():
				record.future.cancel()
		if cancelled_request_ids:
			self._fire_pending_mirror(conversation_id, -len(cancelled_request_ids))
		return cancelled_request_ids

	def resolve_pending_for_conversation(self, conversation_id: str, result_text: str) -> list[str]:
		"""Pop every pending request for this conversation_id and resolve its
		future with result_text (a terminal do-not-retry sentinel), rather than
		cancelling it. Returns the list of request_ids resolved so the caller can
		mark each question's Firebase record cancelled.

		Used by force-end (T-145). A cancelled future surfaces on the agent's MCP
		client as a transport error, which the agent retries (re-stranding it or
		minting orphan state); a resolved future returns result_text as a normal
		value, so the agent gets a semantic terminal signal and stops.
		cancel_pending_for_conversation (true cancel) remains for spawn's
		stale-pending cleanup of a dead prior agent, where there is no live
		awaiter to receive a semantic result."""
		victims = [key for key, record in self._pending.items() if record.conversation_id == conversation_id]
		resolved_request_ids: list[str] = []
		for key in victims:
			record = self._pending.pop(key)
			resolved_request_ids.append(record.request_id)
			if not record.future.done():
				record.future.set_result(result_text)
		if resolved_request_ids:
			self._fire_pending_mirror(conversation_id, -len(resolved_request_ids))
		return resolved_request_ids

	def update_global_away_cache(self, active: bool) -> None:
		"""Listener entry point: update the in-memory cache to reflect a Firebase change."""
		self._global_away = bool(active)

	def global_away(self) -> bool:
		return self._global_away

	def set_pending_mirror(self, callback) -> None:
		"""Callback fires synchronously with (conversation_id, delta) on every pending-count
		mutation. Implementations typically schedule an asyncio task to write
		to Firebase; the callback itself is sync to keep Registry's interface clean."""
		self._pending_mirror = callback

	def _fire_pending_mirror(self, conversation_id: str, delta: int) -> None:
		if self._pending_mirror is None or delta == 0:
			return
		try:
			self._pending_mirror(conversation_id, delta)
		except Exception:
			logging.getLogger(__name__).exception("pending_mirror callback raised")

