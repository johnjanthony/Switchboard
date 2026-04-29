"""In-memory pending-request registry.

All access happens on a single asyncio event loop, so no locking is required.
Pending requests are keyed by (cwd, sender) with supersede semantics: if a
new request arrives for the same (cwd, sender) pair, the prior future is
cancelled and replaced.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from server.collab import CollabSession


@dataclass
class PendingRequest:
	cwd: str
	sender: str
	request_id: str
	future: asyncio.Future[str]
	started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	msg_id: str | None = None


_RECENTLY_ENDED_MAX_AGE_SECONDS = 60.0


class Registry:
	def __init__(self) -> None:
		self._sessions: dict[str, "CollabSession"] = {}
		# Breadcrumbs for recently ended sessions to handle simultaneous call races (E1).
		# Keyed by canonical_cwd, value is (members, ended_at_monotonic). Entries
		# older than _RECENTLY_ENDED_MAX_AGE_SECONDS are pruned on next access —
		# the race window is sub-second; a minute of slack is plenty.
		self._recently_ended: dict[str, tuple[list[str], float]] = {}
		self._pending: dict[tuple[str, str], PendingRequest] = {}
		self.total_answered: int = 0
		self._global_away = False
		self._cwd_overrides: dict[str, bool] = {}
		self._away_mode_callback = None
		self._pending_mirror = None

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

	def add(
		self,
		cwd: str,
		sender: str,
		request_id: str,
		msg_id: str | None = None,
		return_superseded: bool = False,
	) -> asyncio.Future | tuple[asyncio.Future, str | None]:
		"""Add a pending request. If (cwd, sender) is already occupied, the
		prior PendingRequest is superseded: its future is cancelled, the entry
		removed, and the prior request_id returned (when return_superseded=True)
		so callers can mark the prior question's Firebase entry as cancelled.

		Returns the new Future. If return_superseded=True, returns
		(future, prior_request_id_or_None)."""
		key = (cwd, sender)
		prior_request_id = None
		existing = self._pending.pop(key, None)
		if existing is not None:
			prior_request_id = existing.request_id
			if not existing.future.done():
				existing.future.cancel()
			self._fire_pending_mirror(cwd, -1)
		future = asyncio.get_event_loop().create_future()
		self._pending[key] = PendingRequest(
			cwd=cwd,
			sender=sender,
			request_id=request_id,
			future=future,
			msg_id=msg_id,
		)
		self._fire_pending_mirror(cwd, +1)
		if return_superseded:
			return future, prior_request_id
		return future

	def get(self, key: tuple[str, str]) -> "PendingRequest | None":
		return self._pending.get(key)

	def resolve(self, cwd: str, sender: str, text: str) -> str | None:
		"""Resolve the pending request for (cwd, sender). Returns the
		request_id of the resolved entry, or None if no pending exists."""
		key = (cwd, sender)
		record = self._pending.pop(key, None)
		if record is None:
			return None
		if not record.future.done():
			record.future.set_result(text)
		self._fire_pending_mirror(cwd, -1)
		return record.request_id

	def remove(self, cwd: str, sender: str) -> str | None:
		"""Remove the pending entry for (cwd, sender). Cancels the future if
		pending. Returns the request_id of the removed entry, or None."""
		key = (cwd, sender)
		record = self._pending.pop(key, None)
		if record is None:
			return None
		if not record.future.done():
			record.future.cancel()
		self._fire_pending_mirror(cwd, -1)
		return record.request_id

	def all_pending(self) -> list["PendingRequest"]:
		"""Snapshot for bulk-respond on global exit (Slice I)."""
		return list(self._pending.values())

	def pending_for_cwd(self, cwd: str) -> list["PendingRequest"]:
		"""Snapshot of pending requests for a specific channel (Slice L)."""
		return [p for p in self._pending.values() if p.cwd == cwd]

	def cancel_pending_for_cwd(self, cwd: str) -> list[str]:
		"""Pop and cancel every pending request whose cwd matches. Returns the
		list of request_ids that were cancelled so the caller can mark each
		question's Firebase entry cancelled (writing the WITHDRAWN marker).

		Used by spawn-on-cwd to clear stale pendings from a prior agent that
		died without surfacing CancelledError to its tool handler — the MCP
		streamable-HTTP transport doesn't reliably propagate client disconnects."""
		victims = [key for key, record in self._pending.items() if record.cwd == cwd]
		cancelled_request_ids: list[str] = []
		for key in victims:
			record = self._pending.pop(key)
			cancelled_request_ids.append(record.request_id)
			if not record.future.done():
				record.future.cancel()
		if cancelled_request_ids:
			self._fire_pending_mirror(cwd, -len(cancelled_request_ids))
		return cancelled_request_ids

	def add_session(self, session: "CollabSession") -> None:
		self._sessions[session.cwd] = session
		# A new session on a cwd that was recently ended means agents have
		# resumed; clear the breadcrumb so a future end_collab races report
		# correctly against the new session, not the prior.
		self._recently_ended.pop(session.cwd, None)

	def get_session(self, cwd: str) -> "CollabSession | None":
		return self._sessions.get(cwd)

	def remove_session(self, cwd: str) -> None:
		self._sessions.pop(cwd, None)

	def mark_session_ended(self, cwd: str, members: list[str]) -> None:
		"""Record that this cwd had an end_collab call, including who was in the
		session. Used to distinguish 'partner ended first' (E1) from 'never a
		member' (E4) when a second end_collab arrives after the session is
		already purged."""
		import time as _time
		self._recently_ended[cwd] = (members, _time.monotonic())

	def _prune_recently_ended(self) -> None:
		import time as _time
		now = _time.monotonic()
		stale = [
			k for k, (_, ts) in self._recently_ended.items()
			if (now - ts) > _RECENTLY_ENDED_MAX_AGE_SECONDS
		]
		for k in stale:
			self._recently_ended.pop(k, None)

	def get_recently_ended_members(self, cwd: str) -> list[str] | None:
		self._prune_recently_ended()
		entry = self._recently_ended.get(cwd)
		return entry[0] if entry is not None else None

	def was_recently_ended(self, cwd: str) -> bool:
		self._prune_recently_ended()
		return cwd in self._recently_ended

	def is_away_mode_active(self, cwd: str) -> bool:
		# Walk up the canonical path looking for the nearest registered ancestor
		# override. Canonical cwds are forward-slash, lowercased, drive-prefixed
		# (e.g. 'c:/work/switchboard'). A query for 'c:/work/switchboard/android'
		# inherits the override on 'c:/work/switchboard' if no override is set
		# directly on the subdirectory. This matches user mental model: setting
		# at-desk on a project applies to all bash subshells inside it.
		probe = cwd
		while probe:
			if probe in self._cwd_overrides:
				return self._cwd_overrides[probe]
			
			if "/" not in probe:
				# Single-component name or empty (already handled by while-loop), 
				# nothing left to split.
				break

			parent = probe.rsplit("/", 1)[0]
			
			if parent == probe:
				break
			
			# Drive root check: 'c:/'. After rsplit("/", 1) on 'c:/foo', parent is 'c:'.
			# We want to check 'c:/' if 'c:' is hit, but rsplit doesn't give us the slash.
			# If parent is just a drive letter (e.g. 'c:'), it means we reached the root.
			if len(parent) <= 2 and parent.endswith(":"):
				# Check the root override if it exists
				root = parent + "/"
				if root in self._cwd_overrides:
					return self._cwd_overrides[root]
				break

			probe = parent
		return self._global_away

	def set_cwd_override(self, cwd: str, active: bool) -> None:
		# Write unconditionally. The listener will update the cache idempotently.
		self._fire_callback(cwd, active)

	def remove_cwd_override(self, cwd: str) -> None:
		if cwd not in self._cwd_overrides:
			return
		self._fire_callback(cwd, None)

	def update_global_away_cache(self, active: bool) -> None:
		"""Listener entry point: update the in-memory cache to reflect a Firebase change."""
		self._global_away = bool(active)

	def update_cwd_override_cache(self, cwd: str, active: bool | None) -> None:
		"""Listener entry point: set/remove an override in the in-memory cache to reflect Firebase."""
		if active is None:
			self._cwd_overrides.pop(cwd, None)
		else:
			self._cwd_overrides[cwd] = bool(active)

	def cwd_overrides(self) -> dict[str, bool]:
		return dict(self._cwd_overrides)

	def global_away(self) -> bool:
		return self._global_away

	def set_away_mode_callback(self, callback) -> None:
		"""Callback receives (cwd_or_None, active). cwd=None on global flips,
		otherwise the canonical_cwd whose override changed. active=None signals
		that the override entry was removed (mirror should clear it)."""
		self._away_mode_callback = callback

	def _fire_callback(self, cwd: str | None, active: bool | None) -> None:
		if self._away_mode_callback is None:
			return
		try:
			self._away_mode_callback(cwd, active)
		except Exception:
			logging.getLogger(__name__).exception("away_mode_callback raised")

	def set_pending_mirror(self, callback) -> None:
		"""Callback fires synchronously with (cwd, delta) on every pending-count
		mutation. Implementations typically schedule an asyncio task to write
		to Firebase; the callback itself is sync to keep Registry's interface clean."""
		self._pending_mirror = callback

	def _fire_pending_mirror(self, cwd: str, delta: int) -> None:
		if self._pending_mirror is None or delta == 0:
			return
		try:
			self._pending_mirror(cwd, delta)
		except Exception:
			logging.getLogger(__name__).exception("pending_mirror callback raised")
