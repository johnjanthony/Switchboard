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
from typing import Any


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
		if not record.future.done():
			record.future.set_result(text)
		return request_id

	def remove(self, request_id: str) -> None:
		record = self._pending.pop(request_id, None)
		if record is not None:
			self._by_correlation.pop(record.correlation, None)
