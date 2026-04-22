"""Collab session state for two-agent peer collaboration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CollabSession:
	session_id: str
	agent_ids: tuple[str, str]
	task: str
	relay: bool
	created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	_waiting: dict[str, asyncio.Future] = field(default_factory=dict, repr=False)
	_pending: dict[str, list[str]] = field(default_factory=dict, repr=False)
	transcript: list[dict] = field(default_factory=list)

	def other_agent(self, agent_id: str) -> str:
		return self.agent_ids[1] if agent_id == self.agent_ids[0] else self.agent_ids[0]

	def deliver(self, to_agent_id: str, text: str) -> None:
		"""Resolve the waiting agent's future, or buffer if not yet waiting."""
		future = self._waiting.pop(to_agent_id, None)
		if future is not None and not future.done():
			future.set_result(text)
		else:
			self._pending.setdefault(to_agent_id, []).append(text)

	def start_waiting(self, agent_id: str) -> asyncio.Future[str]:
		"""Create a wait future for agent_id. Returns immediately if a message is buffered."""
		pending: str | None = None
		queue = self._pending.get(agent_id)
		if queue:
			pending = queue.pop(0)
			if not queue:
				del self._pending[agent_id]
		else:
			inject_queue = self._pending.get("__inject__")
			if inject_queue:
				pending = inject_queue.pop(0)
				if not inject_queue:
					del self._pending["__inject__"]

		loop = asyncio.get_running_loop()
		future: asyncio.Future[str] = loop.create_future()
		if pending is not None:
			future.set_result(pending)
		else:
			self._waiting[agent_id] = future
		return future

	def cancel_waiting(self, agent_id: str) -> None:
		future = self._waiting.pop(agent_id, None)
		if future is not None and not future.done():
			future.cancel()

	def deliver_inject(self, text: str) -> None:
		"""Deliver a human injection. Resolves a waiting agent's future, or buffers."""
		for agent_id, future in list(self._waiting.items()):
			if not future.done():
				self._waiting.pop(agent_id)
				future.set_result(text)
				return
		self._pending.setdefault("__inject__", []).append(text)
