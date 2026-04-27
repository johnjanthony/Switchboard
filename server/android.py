"""Android MessengerBackend implementation (Placeholder for development)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from server.logging_jsonl import JsonlLogger
from server.messenger import CorrelationToken, IncomingResponse, MessengerBackend


class AndroidBackend(MessengerBackend):
	def __init__(
		self,
		logger: JsonlLogger | None = None,
	) -> None:
		self._logger = logger
		self._response_queue: asyncio.Queue[IncomingResponse] = asyncio.Queue()
		self._command_queue: asyncio.Queue[str] = asyncio.Queue()
		self._pending_questions: dict[str, dict] = {}

	async def aclose(self) -> None:
		pass

	def get_pending_questions(self) -> list[dict]:
		return list(self._pending_questions.values())

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
		title: str | None = None,
		rejected: bool = False,
	) -> tuple[CorrelationToken | None, str | None]:
		msg_id = f"msg_{int(asyncio.get_event_loop().time() * 1000)}"
		if message_type == "question":
			correlation = (channel_id, sender)
			self._pending_questions[request_id] = {
				"request_id": request_id,
				"channel_id": channel_id,
				"question": content,
				"format": format,
				"suggestions": suggestions,
			}
			if self._logger:
				self._logger.info(f"ANDROID_SEND_QUESTION: [{channel_id} | {request_id}] {content}")
			return correlation, msg_id

		if self._logger:
			prefix = f"ANDROID_SEND_{message_type.upper()}"
			self._logger.info(f"{prefix}: [{channel_id}] {content}")
		return None, msg_id

	async def send_timeout_followup(
		self,
		request_id: str,
		channel_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		self._pending_questions.pop(request_id, None)
		if self._logger:
			self._logger.info(f"ANDROID_SEND_TIMEOUT: [{channel_id} | {request_id}]")

	async def send_resolution_confirmation(
		self,
		request_id: str,
		channel_id: str,
		correlation: CorrelationToken,
		response_text: str | None = None,
	) -> None:
		self._pending_questions.pop(request_id, None)
		if self._logger:
			self._logger.info(f"ANDROID_SEND_CONFIRMATION: [{channel_id} | {request_id}]")

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		while True:
			yield await self._response_queue.get()

	async def poll_commands(self) -> AsyncIterator[str]:
		while True:
			yield await self._command_queue.get()

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
		if self._logger:
			self._logger.info(f"ANDROID_SEND_SPAWN_ACK: {channel_id}")

	async def send_text(self, text: str) -> None:
		if self._logger:
			self._logger.info(f"ANDROID_SEND_TEXT: {text}")

	# Method for testing/development to simulate a response from the Android app
	async def simulate_response(self, correlation: CorrelationToken, text: str) -> None:
		await self._response_queue.put(IncomingResponse(correlation=correlation, text=text))
