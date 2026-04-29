"""Collab session state for two-agent peer collaboration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class CollabSession:
	cwd: str
	agent_senders: list[str]
	task: str
	is_byo: bool = False
	created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
	_pre_enroll_msg: str | None = field(default=None, repr=False)
	_pre_enroll_title: str | None = field(default=None, repr=False)
	_waiting: dict[str, asyncio.Future] = field(default_factory=dict, repr=False)
	_pending: dict[str, list[str]] = field(default_factory=dict, repr=False)
	_inject_pending: list[str] = field(default_factory=list, repr=False)
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
		if len(self.agent_senders) < 2:
			raise ValueError("other_sender called on session with fewer than 2 members")
		return self.agent_senders[1] if sender == self.agent_senders[0] else self.agent_senders[0]

	def handle_message(self, sender: str, message: str | None, title: str | None, title_tracker: Any) -> list[tuple[str, str]]:
		deliveries = []
		if message is not None:
			entry = {
				"speaker": sender,
				"message": message,
				"timestamp": datetime.now(timezone.utc).isoformat(),
			}
			self.transcript.append(entry)

			if len(self.agent_senders) == 2:
				if self._pre_enroll_msg is not None:
					pre_msg = self._pre_enroll_msg
					buf_title = self._pre_enroll_title
					self._pre_enroll_msg = None
					self._pre_enroll_title = None
					buf_sender = self.other_sender(sender)
					buf_relayed = title_tracker.maybe_prepend(self.cwd, buf_sender, sender, buf_title, pre_msg)
					self.deliver(sender, buf_relayed)
					deliveries.append((buf_sender, pre_msg))
				
				other = self.other_sender(sender)
				relayed = title_tracker.maybe_prepend(self.cwd, sender, other, title, message)
				self.deliver(other, relayed)
				deliveries.append((sender, message))
			else:
				# Buffer the first message if partner hasn't enrolled yet
				self._pre_enroll_msg = message
				self._pre_enroll_title = title
		else:
			# No message content (empty call). 
			# If this is the second person joining, they drain the pre-enroll buffer.
			if len(self.agent_senders) == 2 and self._pre_enroll_msg is not None:
				pre_msg = self._pre_enroll_msg
				buf_title = self._pre_enroll_title
				self._pre_enroll_msg = None
				self._pre_enroll_title = None
				buf_sender = self.other_sender(sender)
				buf_relayed = title_tracker.maybe_prepend(self.cwd, buf_sender, sender, buf_title, pre_msg)
				self.deliver(sender, buf_relayed)
				deliveries.append((buf_sender, pre_msg))
		return deliveries

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
			# H10: drain the entire queue and coalesce. Without this, a backlog
			# of N buffered messages from the partner delivers FIFO across N
			# successive `start_waiting` calls — the recipient ends up replying
			# to message k while message k+1 is still queued, producing the
			# "stale reply" perception we hit during today's T5/T6 churn.
			# Coalescing delivers the whole backlog as one blob with a markdown
			# horizontal-rule separator so the recipient sees all queued
			# context at once.
			if len(queue) == 1:
				pending = queue.pop(0)
			else:
				pending = "\n\n---\n\n".join(queue)
				queue.clear()
			if not queue:
				del self._pending[sender]
		else:
			if self._inject_pending:
				if len(self._inject_pending) == 1:
					pending = self._inject_pending.pop(0)
				else:
					pending = "\n\n---\n\n".join(self._inject_pending)
					self._inject_pending.clear()

		loop = asyncio.get_running_loop()
		future: asyncio.Future[str] = loop.create_future()

		# H8 Guard: Detect deterministic deadlock (both agents waiting with
		# nothing pending in either direction).
		if pending is None and len(self.agent_senders) == 2:
			partner = self.other_sender(sender)
			if partner in self._waiting:
				err_msg = (
					"ERROR: collab deadlock detected — both agents waiting with "
					"nothing pending. Every mid-session message must carry content."
				)
				# Unblock the partner who is already waiting
				partner_fut = self._waiting.pop(partner)
				if not partner_fut.done():
					partner_fut.set_result(err_msg)
				
				# Return a future that resolves immediately for the current caller
				future.set_result(err_msg)
				return future

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
		self._inject_pending.append(text)

	def has_pending_inject(self) -> bool:
		return bool(self._inject_pending)

	def terminate(self, sentinel: str) -> None:
		"""Resolve every pending _waiting future with the sentinel.

		Called by gateway.end_collab before purging the session. Resolution is
		synchronous from each partner's perspective: their await returns the
		sentinel string immediately."""
		for sender, future in list(self._waiting.items()):
			if not future.done():
				future.set_result(sentinel)
		self._waiting.clear()
