"""MessengerBackend abstract interface and shared types.

The messenger surface is abstracted so the transport can evolve without 
touching the gateway core.
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
	async def write_channel_message(
		self,
		channel_id: str,
		sender: str,
		message_type: str,
		content: str,
		*,
		request_id: str | None = None,
		url: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
		filename: str | None = None,
	) -> "tuple[CorrelationToken | None, str | None]":
		"""Write a message to the channel. Returns (correlation, msg_id).
		correlation is used for message_type='question' to match responses.
		msg_id is the unique ID of the message in the backend."""

	@abstractmethod
	async def send_timeout_followup(
		self,
		request_id: str,
		channel_id: str,
		timeout_seconds: int,
		correlation: "CorrelationToken",
	) -> None:
		"""Inform the developer a pending question has timed out."""

	@abstractmethod
	async def send_resolution_confirmation(
		self,
		request_id: str,
		channel_id: str,
		correlation: "CorrelationToken",
		response_text: str | None = None,
	) -> None:
		"""Confirm to the developer that their response was received."""

	@abstractmethod
	def poll_responses(self) -> "AsyncIterator[IncomingResponse]":
		"""Yield IncomingResponse as replies arrive. Infinite async iterator."""

	@abstractmethod
	def poll_commands(self) -> "AsyncIterator[str]":
		"""Yield slash-commands as they arrive. Infinite async iterator."""

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
		"""Acknowledge a successful spawn command. No-op by default."""
		pass

	async def write_session_meta(
		self,
		channel_id: str,
		type: str,
		project_key: str,
		*,
		agent_senders: list[str] | None = None,
		task: str | None = None,
	) -> None:
		"""Write session metadata to Firebase on session creation. No-op by default."""
		pass

	async def start_inject_listener(self, session_id: str) -> None:
		"""Start listening for human inject messages. No-op by default."""
		pass

	async def poll_inject_messages(self):
		"""Yield (session_id, inject_id, text) tuples. Empty by default."""
		if False:
			yield

	async def write_away_mode_mirror(self, active: bool) -> None:
		"""Mirror the server's away-mode flag to Firebase for the Android app's
		pill-chip listener. No-op by default; backends that expose a Firebase
		surface override this."""
		pass

	@abstractmethod
	async def aclose(self) -> None:
		"""Release any resources held by the backend."""


class MultiBackend(MessengerBackend):
	def __init__(self, backends: list[MessengerBackend]) -> None:
		self._backends = backends

	async def write_channel_message(
		self, channel_id, sender, message_type, content,
		*, request_id=None, url=None, format="plain", suggestions=None, filename=None,
	) -> "tuple[CorrelationToken | None, str | None]":
		results = await asyncio.gather(*(
			b.write_channel_message(
				channel_id, sender, message_type, content,
				request_id=request_id, url=url, format=format, suggestions=suggestions,
				filename=filename,
			)
			for b in self._backends
		))
		
		# For MultiBackend, correlation is a dict mapping backend to its local correlation
		# msg_id is just the first non-None msg_id from any backend
		correlations = {}
		msg_id = None
		for b, res in zip(self._backends, results):
			# handle both old (corr) and new (corr, mid) return types for robustness
			if isinstance(res, tuple):
				corr, mid = res
			else:
				corr, mid = res, None

			if message_type == "question":
				correlations[b] = corr
			if mid:
				msg_id = mid
				
		return (correlations if message_type == "question" else None), msg_id

	async def send_timeout_followup(
		self, request_id, channel_id, timeout_seconds, correlation
	) -> None:
		if isinstance(correlation, dict):
			await asyncio.gather(*(
				b.send_timeout_followup(request_id, channel_id, timeout_seconds, correlation[b])
				for b in self._backends if b in correlation
			))
		else:
			await asyncio.gather(*(
				b.send_timeout_followup(request_id, channel_id, timeout_seconds, correlation)
				for b in self._backends
			))

	async def send_resolution_confirmation(
		self, request_id, channel_id, correlation, response_text=None
	) -> None:
		if isinstance(correlation, dict):
			await asyncio.gather(*(
				b.send_resolution_confirmation(request_id, channel_id, correlation[b], response_text=response_text)
				for b in self._backends if b in correlation
			))
		else:
			await asyncio.gather(*(
				b.send_resolution_confirmation(request_id, channel_id, correlation, response_text=response_text)
				for b in self._backends
			))

	async def write_response_text(self, channel_id: str, msg_id: str, text: str) -> None:
		await asyncio.gather(*(
			b.write_response_text(channel_id, msg_id, text)
			for b in self._backends if hasattr(b, "write_response_text")
		))

	async def poll_responses(self) -> "AsyncIterator[IncomingResponse]":
		combined: asyncio.Queue = asyncio.Queue()

		async def _forward(b: MessengerBackend):
			async for resp in b.poll_responses():
				await combined.put((b, resp))

		tasks = [asyncio.create_task(_forward(b)) for b in self._backends]
		try:
			while True:
				backend, resp = await combined.get()
				yield IncomingResponse(
					correlation=(backend, resp.correlation), text=resp.text
				)
		finally:
			for t in tasks:
				t.cancel()

	async def poll_commands(self) -> "AsyncIterator[str]":
		combined: asyncio.Queue = asyncio.Queue()

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

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
		await asyncio.gather(*(b.send_spawn_ack(channel_id, prompt) for b in self._backends))

	async def write_session_meta(
		self, channel_id, type, project_key, *, agent_senders=None, task=None
	) -> None:
		await asyncio.gather(*(
			b.write_session_meta(channel_id, type, project_key, agent_senders=agent_senders, task=task)
			for b in self._backends
		))

	async def start_inject_listener(self, session_id) -> None:
		await asyncio.gather(*(b.start_inject_listener(session_id) for b in self._backends))

	async def write_away_mode_mirror(self, active: bool) -> None:
		await asyncio.gather(*(b.write_away_mode_mirror(active) for b in self._backends))

	async def aclose(self) -> None:
		await asyncio.gather(*(b.aclose() for b in self._backends))
