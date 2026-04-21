"""Firebase MessengerBackend implementation."""

from __future__ import annotations

import asyncio
import time
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
		# Normalize bucket name: remove gs:// prefix if present
		if storage_bucket and storage_bucket.startswith("gs://"):
			self._storage_bucket = storage_bucket[5:]
		else:
			self._storage_bucket = storage_bucket

		self._initialized = False
		try:
			# Check if already initialized to avoid error
			firebase_admin.get_app()
			self._initialized = True
		except ValueError:
			cred = credentials.Certificate(service_account_json)
			firebase_admin.initialize_app(cred, {
				'databaseURL': database_url,
				'storageBucket': self._storage_bucket
			})
			self._initialized = True

		self._db_ref = db.reference('questions')
		self._resp_ref = db.reference('responses')
		self._cmd_ref = db.reference('commands')
		self._doc_ref = db.reference('documents')
		self._session_ref = db.reference('sessions')
		self._response_queue: asyncio.Queue[IncomingResponse] = asyncio.Queue()
		self._command_queue: asyncio.Queue[str] = asyncio.Queue()
		self._loop = asyncio.get_running_loop()
		self._resp_listener = None
		self._cmd_listener = None

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

	def _on_command(self, event):
		"""Callback from Firebase thread for commands."""
		if event.event_type == 'put' and event.data:
			# Commands are expected to be written as a child or root value
			path = event.path.strip('/')
			if not path and isinstance(event.data, dict):
				for cmd_id, text in event.data.items():
					if isinstance(text, str):
						self._loop.call_soon_threadsafe(
							self._enqueue_command, cmd_id, text
						)
			elif path and isinstance(event.data, str):
				self._loop.call_soon_threadsafe(
					self._enqueue_command, path, event.data
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

	def _enqueue_command(self, command_id: str, text: str):
		asyncio.create_task(self._command_queue.put(text))
		# Clean up the command after picking it up
		def _cleanup():
			self._cmd_ref.child(command_id).delete()

		self._loop.run_in_executor(None, _cleanup)

	async def aclose(self) -> None:
		if self._resp_listener:
			self._resp_listener.close()
		if self._cmd_listener:
			self._cmd_listener.close()

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
			"status": "pending",
			"created_at": int(time.time() * 1000)
		}

		# Blocking call, run in executor
		await self._loop.run_in_executor(
			None, lambda: self._db_ref.child(request_id).set(data)
		)

		# Update session state to open
		session_data = {
			"state": "open",
			"last_activity": int(time.time() * 1000)
		}
		await self._loop.run_in_executor(
			None, lambda: self._session_ref.child(agent_id).update(session_data)
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
		if not self._resp_listener:
			self._resp_listener = self._resp_ref.listen(self._on_response)

		while True:
			yield await self._response_queue.get()

	async def poll_commands(self) -> AsyncIterator[str]:
		if not self._cmd_listener:
			self._cmd_listener = self._cmd_ref.listen(self._on_command)

		while True:
			yield await self._command_queue.get()

	async def send_spawn_ack(self, project_key: str, prompt: str | None) -> None:
		# For Firebase, we can just send a notification
		message = f"Spawned session in {project_key}"
		if prompt:
			message += f": {prompt}"
		await self.send_notification("System", message)

	async def send_document(
		self, agent_id: str, path: Path, caption: str | None
	) -> None:
		if not self._initialized:
			return

		filename = path.name
		timestamp = int(time.time())
		storage_path = f"documents/{agent_id}/{timestamp}_{filename}"

		def _upload():
			bucket = storage.bucket(self._storage_bucket)
			blob = bucket.blob(storage_path)
			blob.upload_from_filename(str(path))
			# Generate a signed URL valid for 7 days
			return blob.generate_signed_url(expiration=timedelta(days=7))

		from datetime import timedelta
		url = await self._loop.run_in_executor(None, _upload)

		doc_data = {
			"agent_id": agent_id,
			"filename": filename,
			"url": url,
			"caption": caption or "",
			"status": "unread",
			"timestamp": timestamp * 1000
		}

		await self._loop.run_in_executor(
			None, lambda: self._doc_ref.push().set(doc_data)
		)

		# Update session state to open
		session_data = {
			"state": "open",
			"last_activity": int(time.time() * 1000)
		}
		await self._loop.run_in_executor(
			None, lambda: self._session_ref.child(agent_id).update(session_data)
		)

		# Also send a notification
		notification_body = f"New document: {filename}"
		if caption:
			notification_body += f" - {caption}"

		await self.send_notification(agent_id, notification_body)
