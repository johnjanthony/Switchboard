"""In-memory pending-request registry.

All access happens on a single asyncio event loop, so no locking is required.
The secondary correlation index lets a messenger backend resolve a response
using whatever opaque token it stored at send time (Telegram message_id,
Firebase doc path, etc.) without knowing the request_id.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from server.collab import CollabSession


@dataclass
class PendingRequest:
	request_id: str
	agent_id: str
	correlation: Any
	future: asyncio.Future[str]
	created_at: datetime = field(
		default_factory=lambda: datetime.now(timezone.utc)
	)


class Registry:
	def __init__(self) -> None:
		self._pending: dict[str, PendingRequest] = {}
		self._by_correlation: dict[Any, str] = {}
		self.total_answered: int = 0
		self._sessions: dict[str, "CollabSession"] = {}
		self._agent_to_session: dict[str, str] = {}

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
		self, request_id: str, agent_id: str, correlation: Any
	) -> asyncio.Future[str]:
		loop = asyncio.get_running_loop()
		future: asyncio.Future[str] = loop.create_future()
		self._pending[request_id] = PendingRequest(
			request_id=request_id,
			agent_id=agent_id,
			correlation=correlation,
			future=future,
		)

		if isinstance(correlation, dict):
			for b, c in correlation.items():
				self._by_correlation[(b, c)] = request_id
		else:
			self._by_correlation[correlation] = request_id

		return future

	def get(self, request_id: str) -> PendingRequest | None:
		return self._pending.get(request_id)

	def resolve_by_correlation(
		self, correlation: Any, text: str
	) -> str | None:
		request_id = self._by_correlation.pop(correlation, None)
		if request_id is None:
			return None

		record = self._pending.pop(request_id, None)
		if record is None:
			return None

		# If it was a multi-correlation, clean up the other backends
		if isinstance(record.correlation, dict):
			for b, c in record.correlation.items():
				self._by_correlation.pop((b, c), None)
		else:
			# Just in case there were other mappings to this request_id (not expected in current design but for robustness)
			pass

		if not record.future.done():
			record.future.set_result(text)
		self.total_answered += 1
		return request_id

	def remove(self, request_id: str) -> None:
		record = self._pending.pop(request_id, None)
		if record is not None:
			if isinstance(record.correlation, dict):
				for b, c in record.correlation.items():
					self._by_correlation.pop((b, c), None)
			else:
				self._by_correlation.pop(record.correlation, None)

	def add_session(self, session: "CollabSession") -> None:
		existing = self._sessions.get(session.session_id)
		if existing is not None:
			for agent_id in existing.agent_ids:
				self._agent_to_session.pop(agent_id, None)
		self._sessions[session.session_id] = session
		for agent_id in session.agent_ids:
			self._agent_to_session[agent_id] = session.session_id

	def get_session(self, session_id: str) -> "CollabSession | None":
		return self._sessions.get(session_id)

	def get_session_for_agent(self, agent_id: str) -> "CollabSession | None":
		session_id = self._agent_to_session.get(agent_id)
		return self._sessions.get(session_id) if session_id else None

	def remove_session(self, session_id: str) -> None:
		session = self._sessions.pop(session_id, None)
		if session is not None:
			for agent_id in session.agent_ids:
				self._agent_to_session.pop(agent_id, None)
