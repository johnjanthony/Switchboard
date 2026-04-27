"""Collab session state for two-agent peer collaboration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class CollabSession:
	cwd: str
	agent_senders: list[str]
	task: str
	is_byo: bool = False
	created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	_pre_enroll_msg: str | None = field(default=None, repr=False)
	_waiting: dict[str, asyncio.Future] = field(default_factory=dict, repr=False)
	_pending: dict[str, list[str]] = field(default_factory=dict, repr=False)
	transcript: list[dict] = field(default_factory=list)

	def enroll(self, sender: str) -> str | None:
		if sender in self.agent_senders:
			if len(self.agent_senders) >= 2:
				return None  # idempotent — already fully enrolled
			return "duplicate"
		if len(self.agent_senders) >= 2:
			return "full"
		self.agent_senders.append(sender)
		return None

	def other_sender(self, sender: str) -> str:
		return self.agent_senders[1] if sender == self.agent_senders[0] else self.agent_senders[0]

	def deliver(self, to_sender: str, text: str) -> None:
		future = self._waiting.pop(to_sender, None)
		if future is not None and not future.done():
			future.set_result(text)
		else:
			self._pending.setdefault(to_sender, []).append(text)

	def start_waiting(self, sender: str) -> asyncio.Future[str]:
		pending: str | None = None
		queue = self._pending.get(sender)
		if queue:
			pending = queue.pop(0)
			if not queue:
				del self._pending[sender]
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
			self._waiting[sender] = future
		return future

	def cancel_waiting(self, sender: str) -> None:
		future = self._waiting.pop(sender, None)
		if future is not None and not future.done():
			future.cancel()

	def deliver_inject(self, text: str) -> None:
		for sender, future in list(self._waiting.items()):
			if not future.done():
				self._waiting.pop(sender)
				future.set_result(text)
				return
		self._pending.setdefault("__inject__", []).append(text)

	def has_pending_inject(self) -> bool:
		return bool(self._pending.get("__inject__"))

	def terminate(self, sentinel: str) -> None:
		"""Resolve every pending _waiting future with the sentinel.

		Called by gateway.end_collab before purging the session. Resolution is
		synchronous from each partner's perspective: their await returns the
		sentinel string immediately."""
		for sender, future in list(self._waiting.items()):
			if not future.done():
				future.set_result(sentinel)
		self._waiting.clear()
