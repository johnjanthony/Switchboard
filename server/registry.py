"""In-memory pending-request registry.

All access happens on a single asyncio event loop, so no locking is required.
The secondary correlation index lets a messenger backend resolve a response
using whatever opaque token it stored at send time (Firebase doc path, etc.)
without knowing the request_id.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from server.collab import CollabSession


@dataclass
class PendingRequest:
	request_id: str
	channel_id: str
	correlation: Any
	future: asyncio.Future[str]
	msg_id: str | None = None
	created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Registry:
	def __init__(self, away_mode_path: Path | None = None) -> None:
		self._pending: dict[str, PendingRequest] = {}
		self._by_correlation: dict[Any, str] = {}
		self.total_answered: int = 0
		self._sessions: dict[str, "CollabSession"] = {}
		self._away_mode_path = away_mode_path
		self._away_mode_active, self._away_mode_entered_at = self._load_away_mode()
		self._away_mode_callback = None

	@property
	def pending_count(self) -> int:
		return len(self._pending)

	@property
	def oldest_pending_age_seconds(self) -> float | None:
		if not self._pending:
			return None
		now = datetime.now(timezone.utc)
		oldest = min(r.created_at for r in self._pending.values())
		return (now - oldest).total_seconds()

	def add(
		self, request_id: str, channel_id: str, correlation: Any, msg_id: str | None = None
	) -> asyncio.Future[str]:
		loop = asyncio.get_running_loop()
		future: asyncio.Future[str] = loop.create_future()
		self._pending[request_id] = PendingRequest(
			request_id=request_id,
			channel_id=channel_id,
			correlation=correlation,
			future=future,
			msg_id=msg_id,
		)
		if correlation is not None:
			if isinstance(correlation, dict):
				for b, c in correlation.items():
					self._by_correlation[(b, c)] = request_id
			else:
				self._by_correlation[correlation] = request_id
		return future

	def get(self, request_id: str) -> PendingRequest | None:
		return self._pending.get(request_id)

	def resolve_by_correlation(self, correlation: Any, text: str) -> PendingRequest | None:
		request_id = self._by_correlation.pop(correlation, None)
		if request_id is None:
			return None
		record = self._pending.pop(request_id, None)
		if record is None:
			return None
		if isinstance(record.correlation, dict):
			for b, c in record.correlation.items():
				self._by_correlation.pop((b, c), None)
		if not record.future.done():
			record.future.set_result(text)
		self.total_answered += 1
		return record

	def remove(self, request_id: str) -> None:
		record = self._pending.pop(request_id, None)
		if record is not None:
			if isinstance(record.correlation, dict):
				for b, c in record.correlation.items():
					self._by_correlation.pop((b, c), None)
			else:
				self._by_correlation.pop(record.correlation, None)

	def add_session(self, session: "CollabSession") -> None:
		self._sessions[session.session_id] = session

	def get_session(self, session_id: str) -> "CollabSession | None":
		return self._sessions.get(session_id)

	def remove_session(self, session_id: str) -> None:
		self._sessions.pop(session_id, None)

	def is_away_mode_active(self) -> bool:
		return self._away_mode_active

	def set_away_mode(self, active: bool) -> None:
		self._away_mode_active = active
		self._away_mode_entered_at = (
			datetime.now(timezone.utc) if active else None
		)
		self._persist_away_mode()
		if self._away_mode_callback is not None:
			try:
				self._away_mode_callback(active)
			except Exception:
				# Callback failures never propagate back to the toggler, but we log them
				# so operators can see if the Firebase mirror stopped updating.
				logging.getLogger(__name__).exception("away_mode_callback raised")

	def set_away_mode_callback(self, callback) -> None:
		"""Register a post-set callback invoked with the new active value after
		in-memory state and sidecar are both updated. Single-slot; the latest
		registration wins. Pass None to clear."""
		self._away_mode_callback = callback

	def _load_away_mode(self) -> tuple[bool, datetime | None]:
		if self._away_mode_path is None or not self._away_mode_path.exists():
			return False, None
		try:
			data = json.loads(self._away_mode_path.read_text(encoding="utf-8"))
			active = bool(data.get("active", False))
			entered_raw = data.get("entered_at")
			entered = (
				datetime.fromisoformat(entered_raw)
				if isinstance(entered_raw, str)
				else None
			)
			return active, entered
		except Exception:
			return False, None

	def _persist_away_mode(self) -> None:
		if self._away_mode_path is None:
			return
		payload = {
			"active": self._away_mode_active,
			"entered_at": (
				self._away_mode_entered_at.isoformat()
				if self._away_mode_entered_at is not None
				else None
			),
		}
		self._away_mode_path.parent.mkdir(parents=True, exist_ok=True)
		self._away_mode_path.write_text(
			json.dumps(payload), encoding="utf-8"
		)
