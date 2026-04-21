"""MessengerBackend abstract interface and shared types.

The messenger surface is abstracted so the transport (Telegram now, Firebase
later) can evolve without touching the gateway core. Concrete impls live in
their own modules (e.g. `server/telegram.py`).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
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
		self,
		request_id: str,
		agent_id: str,
		question: str,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> CorrelationToken:
		"""Deliver the question. Return a backend-specific token that
		will be matched against `IncomingResponse.correlation` later."""

	@abstractmethod
	async def send_notification(self, agent_id: str, message: str, format: str = "plain") -> None:
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

	@abstractmethod
	async def send_document(
		self, agent_id: str, path: Path, caption: str | None
	) -> None:
		"""Deliver a file to the developer. Fire-and-forget; no reply tracking."""

	@abstractmethod
	def poll_commands(self) -> AsyncIterator[str]:
		"""Yield slash-commands as they arrive. Infinite async iterator."""

	async def send_spawn_ack(self, project_key: str, prompt: str | None) -> None:
		"""Acknowledge a successful spawn command."""
		pass

	@abstractmethod
	async def aclose(self) -> None:
		"""Release any resources held by the backend."""


class MultiBackend(MessengerBackend):
	"""A composite backend that broadcasts to multiple child backends.

	Responses from any child backend resolve the request.
	"""

	def __init__(self, backends: list[MessengerBackend]) -> None:
		self._backends = backends

	async def send_question(
		self,
		request_id: str,
		agent_id: str,
		question: str,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> CorrelationToken:
		# For MultiBackend, the correlation token is a dict mapping backend to its token
		correlations = {}
		for b in self._backends:
			correlations[b] = await b.send_question(
				request_id, agent_id, question, format, suggestions
			)
		return correlations

	async def send_notification(self, agent_id: str, message: str, format: str = "plain") -> None:
		await asyncio.gather(
			*(b.send_notification(agent_id, message, format) for b in self._backends)
		)

	async def send_timeout_followup(
		self,
		request_id: str,
		agent_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		# correlation is a dict[MessengerBackend, CorrelationToken]
		await asyncio.gather(
			*(
				b.send_timeout_followup(
					request_id, agent_id, timeout_seconds, correlation[b]
				)
				for b in self._backends
				if b in correlation
			)
		)

	async def send_resolution_confirmation(
		self,
		request_id: str,
		agent_id: str,
		correlation: CorrelationToken,
	) -> None:
		await asyncio.gather(
			*(
				b.send_resolution_confirmation(request_id, agent_id, correlation[b])
				for b in self._backends
				if b in correlation
			)
		)

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		async def _poll(b: MessengerBackend):
			async for resp in b.poll_responses():
				# We yield a response where correlation is a tuple of (backend, backend_correlation)
				# so that MultiBackend can be nested or simply tracked.
				# Actually, the Registry needs to know how to resolve it.
				yield (b, resp)

		# Merge streams from all backends
		queues: list[asyncio.Queue] = [asyncio.Queue() for _ in self._backends]

		async def _forward(b: MessengerBackend, q: asyncio.Queue):
			async for resp in b.poll_responses():
				await q.put((b, resp))

		tasks = [
			asyncio.create_task(_forward(b, q))
			for b, q in zip(self._backends, queues)
		]

		try:
			# Use a combined queue to yield from
			combined = asyncio.Queue()

			async def _to_combined(q: asyncio.Queue):
				while True:
					item = await q.get()
					await combined.put(item)

			forward_tasks = [
				asyncio.create_task(_to_combined(q)) for q in queues
			]

			while True:
				backend, resp = await combined.get()
				# Return a correlation that Registry can use.
				# If Registry is updated to handle dict correlations, we need to be careful.
				# Here we just yield a correlation that matches what we expect in resolve_by_correlation.
				yield IncomingResponse(
					correlation=(backend, resp.correlation), text=resp.text
				)
		finally:
			for t in tasks + forward_tasks:
				t.cancel()

	async def poll_commands(self) -> AsyncIterator[str]:
		combined = asyncio.Queue()

		async def _forward(b: MessengerBackend):
			async for cmd in b.poll_commands():
				await combined.put(cmd)

		tasks = [asyncio.create_task(_forward(b)) for b in self._backends]
		try:
			while True:
				yield await combined.get()
		finally:
			for t in tasks:
				t.cancel()

	async def send_spawn_ack(self, project_key: str, prompt: str | None) -> None:
		await asyncio.gather(
			*(b.send_spawn_ack(project_key, prompt) for b in self._backends)
		)

	async def send_document(
		self, agent_id: str, path: Path, caption: str | None
	) -> None:
		await asyncio.gather(
			*(b.send_document(agent_id, path, caption) for b in self._backends)
		)

	async def aclose(self) -> None:
		await asyncio.gather(*(b.aclose() for b in self._backends))
