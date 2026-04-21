"""Firebase MessengerBackend implementation."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncIterator

import firebase_admin
from firebase_admin import credentials, db, messaging

from server.logging_jsonl import JsonlLogger
from server.messenger import CorrelationToken, IncomingResponse, MessengerBackend


class FirebaseBackend(MessengerBackend):
	def __init__(
		self,
		service_account_json: str,
		database_url: str,
		logger: JsonlLogger | None = None,
	) -> None:
		self._logger = logger
		self._initialized = False
		try:
			# Check if already initialized to avoid error
			firebase_admin.get_app()
			self._initialized = True
		except ValueError:
			cred = credentials.Certificate(service_account_json)
			firebase_admin.initialize_app(cred, {
				'databaseURL': database_url
			})
			self._initialized = True

		self._db_ref = db.reference('questions')
		self._resp_ref = db.reference('responses')
		self._response_queue: asyncio.Queue[IncomingResponse] = asyncio.Queue()
		self._command_queue: asyncio.Queue[str] = asyncio.Queue()
		self._loop = asyncio.get_running_loop()
		self._listener = None

	def _on_response(self, event):
		"""Callback from Firebase thread."""
		if event.event_type == 'put' and event.data:
			# Data might be {request_id: {text: "...", timestamp: ...}}
			# or just {text: "...", timestamp: ...} if path is specific
			path = event.path.strip('/')
			if not path and isinstance(event.data, dict):
				# Root level update
				for req_id, data in event.data.items():
					if isinstance(data, dict) and 'text' in data:
						self._loop.call_soon_threadsafe(
							self._enqueue_response, req_id, data['text']
						)
			elif path and isinstance(event.data, dict) and 'text' in event.data:
				# Specific request update
				self._loop.call_soon_threadsafe(
					self._enqueue_response, path, event.data['text']
				)

	def _enqueue_response(self, request_id: str, text: str):
		asyncio.create_task(self._response_queue.put(
			IncomingResponse(correlation=f"firebase_{request_id}", text=text)
		))
		# Clean up the response from DB after picking it up
		def _cleanup():
			self._resp_ref.child(request_id).delete()

		# Run cleanup in executor since it's blocking
		self._loop.run_in_executor(None, _cleanup)

	async def aclose(self) -> None:
		if self._listener:
			self._listener.close()

	async def send_question(
		self,
		request_id: str,
		agent_id: str,
		question: str,
		format: str = "plain",
		suggestions: list[str] | None = None,
	) -> CorrelationToken:
		data = {
			"request_id": request_id,
			"agent_id": agent_id,
			"question": question,
			"format": format,
			"suggestions": suggestions,
			"created_at": int(time.time() * 1000)
		}

		# Blocking call, run in executor
		await self._loop.run_in_executor(
			None, lambda: self._db_ref.child(request_id).set(data)
		)

		# Send Push Notification
		notification = messaging.Notification(
			title=f"Question from {agent_id}",
			body=question[:100] + ("..." if len(question) > 100 else "")
		)
		message = messaging.Message(
			notification=notification,
			topic="questions",
			data={
				"request_id": request_id,
				"agent_id": agent_id
			}
		)
		try:
			await self._loop.run_in_executor(
				None, lambda: messaging.send(message)
			)
		except Exception as exc:
			if self._logger:
				self._logger.surface_error(f"firebase_fcm_error: {exc}")

		return f"firebase_{request_id}"

	async def send_notification(self, agent_id: str, message: str, format: str = "plain") -> None:
		# Just push a notification
		notification = messaging.Notification(
			title=f"Update from {agent_id}",
			body=message[:100] + ("..." if len(message) > 100 else "")
		)
		msg = messaging.Message(
			notification=notification,
			topic="notifications"
		)
		try:
			await self._loop.run_in_executor(
				None, lambda: messaging.send(msg)
			)
		except Exception as exc:
			if self._logger:
				self._logger.surface_error(f"firebase_fcm_notification_error: {exc}")

	async def send_timeout_followup(
		self,
		request_id: str,
		agent_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		# Remove from DB
		await self._loop.run_in_executor(
			None, lambda: self._db_ref.child(request_id).delete()
		)

	async def send_resolution_confirmation(
		self,
		request_id: str,
		agent_id: str,
		correlation: CorrelationToken,
	) -> None:
		# Remove from DB
		await self._loop.run_in_executor(
			None, lambda: self._db_ref.child(request_id).delete()
		)

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		if not self._listener:
			self._listener = self._resp_ref.listen(self._on_response)

		while True:
			yield await self._response_queue.get()

	async def poll_commands(self) -> AsyncIterator[str]:
		while True:
			yield await self._command_queue.get()

	async def send_document(
		self, agent_id: str, path: Path, caption: str | None
	) -> None:
		# Not implemented for Firebase yet, or could send a link/notification
		pass
