"""Firebase MessengerBackend implementation."""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from pathlib import Path
from typing import AsyncIterator

import firebase_admin
from firebase_admin import credentials, db, messaging, storage

from server.logging_jsonl import JsonlLogger
from server.messenger import CorrelationToken, IncomingResponse, MessengerBackend


class FirebaseBackend(MessengerBackend):
	def __init__(
		self,
		service_account_json: str,
		database_url: str,
		storage_bucket: str | None = None,
		logger: JsonlLogger | None = None,
	) -> None:
		self._logger = logger
		if storage_bucket and storage_bucket.startswith("gs://"):
			self._storage_bucket = storage_bucket[5:]
		else:
			self._storage_bucket = storage_bucket

		self._initialized = False
		try:
			firebase_admin.get_app()
			self._initialized = True
		except ValueError:
			cred = credentials.Certificate(service_account_json)
			firebase_admin.initialize_app(cred, {
				'databaseURL': database_url,
				'storageBucket': self._storage_bucket,
			})
			self._initialized = True

		self._resp_ref = db.reference('responses')
		self._cmd_ref = db.reference('commands')
		self._session_ref = db.reference('sessions')
		self._response_queue: asyncio.Queue[IncomingResponse] = asyncio.Queue()
		self._command_queue: asyncio.Queue[str] = asyncio.Queue()
		self._loop = asyncio.get_running_loop()
		self._resp_listener = None
		self._cmd_listener = None
		self._inject_queue_internal: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
		self._inject_listeners: dict[str, object] = {}

	def _on_response(self, event):
		if event.event_type == 'put' and event.data:
			path = event.path.strip('/')
			if not path and isinstance(event.data, dict):
				for req_id, data in event.data.items():
					if isinstance(data, dict) and 'text' in data:
						self._loop.call_soon_threadsafe(
							self._enqueue_response, req_id, data['text']
						)
			elif path and isinstance(event.data, dict) and 'text' in event.data:
				self._loop.call_soon_threadsafe(
					self._enqueue_response, path, event.data['text']
				)

	def _on_command(self, event):
		if event.event_type == 'put' and event.data:
			path = event.path.strip('/')
			if not path and isinstance(event.data, dict):
				for cmd_id, text in event.data.items():
					if isinstance(text, str):
						self._loop.call_soon_threadsafe(self._enqueue_command, cmd_id, text)
			elif path and isinstance(event.data, str):
				self._loop.call_soon_threadsafe(self._enqueue_command, path, event.data)

	def _enqueue_response(self, request_id: str, text: str):
		asyncio.create_task(self._response_queue.put(
			IncomingResponse(correlation=f"firebase_{request_id}", text=text)
		))

	def _enqueue_command(self, command_id: str, text: str):
		asyncio.create_task(self._command_queue.put(text))
		def _cleanup():
			self._cmd_ref.child(command_id).delete()
		self._loop.run_in_executor(None, _cleanup)

	async def aclose(self) -> None:
		if self._resp_listener:
			self._resp_listener.close()
		if self._cmd_listener:
			self._cmd_listener.close()
		for listener in self._inject_listeners.values():
			listener.close()
		self._inject_listeners.clear()

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
	) -> CorrelationToken | None:
		msg_id = _uuid.uuid4().hex[:8]
		timestamp = int(time.time() * 1000)
		data: dict = {
			"sender": sender,
			"message_type": message_type,
			"content": content,
			"timestamp": timestamp,
		}
		if request_id is not None:
			data["request_id"] = request_id
		if url is not None:
			data["url"] = url

		await self._loop.run_in_executor(
			None,
			lambda: self._session_ref.child(channel_id).child("messages").child(msg_id).set(data),
		)

		# FCM push
		if message_type == "question":
			fcm_data = {"request_id": request_id or "", "channel_id": channel_id}
			notif = messaging.Notification(
				title=f"Question from {sender}",
				body=content[:100] + ("..." if len(content) > 100 else ""),
			)
			msg = messaging.Message(notification=notif, topic="questions", data=fcm_data)
		else:
			fcm_data = {"channel_id": channel_id, "message_type": message_type}
			notif = messaging.Notification(
				title=f"Update from {sender}",
				body=content[:100] + ("..." if len(content) > 100 else ""),
			)
			msg = messaging.Message(notification=notif, topic="notifications", data=fcm_data)

		try:
			await self._loop.run_in_executor(None, lambda: messaging.send(msg))
		except Exception as exc:
			if self._logger:
				self._logger.surface_error(f"firebase_fcm_error: {exc}")

		if message_type == "question":
			return f"firebase_{request_id}"
		return None

	async def send_timeout_followup(
		self,
		request_id: str,
		channel_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		# Write a system notify to the channel so the developer sees it in the app
		try:
			await self.write_channel_message(
				channel_id, "system", "notify",
				f"Question timed out after {timeout_seconds}s (request: {request_id})",
			)
		except Exception as exc:
			if self._logger:
				self._logger.surface_error(f"firebase_timeout_notify_error: {exc}")

	async def send_resolution_confirmation(
		self,
		request_id: str,
		channel_id: str,
		correlation: CorrelationToken,
	) -> None:
		pass  # response is already written to responses/{request_id} by the Android app

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		if not self._resp_listener:
			self._resp_listener = self._resp_ref.listen(self._on_response)
		while True:
			yield await self._response_queue.get()

	async def poll_commands(self) -> AsyncIterator[str]:
		if not self._cmd_listener:
			self._cmd_listener = self._cmd_ref.listen(self._on_command)
		while True:
			yield await self._command_queue.get()

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
		msg = f"Spawned: `{channel_id}`"
		if prompt:
			msg += f"\n{prompt[:80]}"
		await self.write_channel_message(channel_id, "system", "notify", msg, format="markdown")

	async def write_session_meta(
		self,
		channel_id: str,
		type: str,
		project_key: str,
		*,
		agent_senders: list[str] | None = None,
		task: str | None = None,
	) -> None:
		meta: dict = {
			"type": type,
			"project_key": project_key,
			"created_at": int(time.time() * 1000),
		}
		if agent_senders is not None:
			meta["agent_senders"] = agent_senders
		if task is not None:
			meta["task"] = task
		await self._loop.run_in_executor(
			None,
			lambda: self._session_ref.child(channel_id).child("meta").set(meta),
		)

	async def start_inject_listener(self, session_id: str) -> None:
		if session_id in self._inject_listeners:
			return
		inject_ref = db.reference(f"sessions/{session_id}/inject_queue")

		def _on_inject(event):
			if event.event_type != "put" or not event.data:
				return
			path = event.path.strip("/")
			data = event.data
			if path and isinstance(data, dict) and "content" in data:
				self._loop.call_soon_threadsafe(
					self._inject_queue_internal.put_nowait,
					(session_id, path, data["content"]),
				)

		self._inject_listeners[session_id] = inject_ref.listen(_on_inject)

	async def poll_inject_messages(self):
		while True:
			yield await self._inject_queue_internal.get()
