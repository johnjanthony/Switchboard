"""MessengerBackend abstract interface and shared types.

The messenger surface is abstracted so the transport (Telegram now, Firebase
later) can evolve without touching the gateway core. Concrete impls live in
their own modules (e.g. `server/telegram.py`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

CorrelationToken = Any


@dataclass
class IncomingResponse:
	"""A response arriving from the messenger backend.

	`correlation` is whatever opaque token the backend stored at
	`send_question` time (e.g. Telegram message_id). The gateway uses it
	to look up the pending request_id in the registry.
	"""

	correlation: CorrelationToken
	text: str


class MessengerBackend(ABC):
	@abstractmethod
	async def send_question(
		self, request_id: str, agent_id: str, question: str
	) -> CorrelationToken:
		"""Deliver the question. Return a backend-specific token that
		will be matched against `IncomingResponse.correlation` later."""

	@abstractmethod
	async def send_notification(self, agent_id: str, message: str) -> None:
		"""Fire-and-forget status update; no reply tracking."""

	@abstractmethod
	async def send_timeout_followup(
		self,
		request_id: str,
		agent_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		"""Inform the developer a pending question has timed out."""

	@abstractmethod
	async def send_resolution_confirmation(
		self,
		request_id: str,
		agent_id: str,
		correlation: CorrelationToken,
	) -> None:
		"""Confirm to the developer that their response was received."""

	@abstractmethod
	def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		"""Yield IncomingResponse as replies arrive. Infinite async
		iterator; the caller cancels the task to stop polling."""
