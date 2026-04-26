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
		def _cleanup():
			self._resp_ref.child(request_id).delete()
		self._loop.run_in_executor(None, _cleanup)

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
		filename: str | None = None,
	) -> tuple[CorrelationToken | None, str | None]:
		# Ensure meta exists so the Android app discovers the channel properly
		await self._write_default_meta_if_missing(channel_id)

		# If this is a document and we have a local path instead of a URL, upload it
		effective_url = url
		effective_filename = filename
		if message_type == "document" and url and not (url.startswith("http://") or url.startswith("https://")):
			try:
				path = Path(url)
				effective_url = await self._upload_file(path)
				if effective_filename is None:
					effective_filename = path.name
				if self._logger:
					self._logger.surface_error(f"firebase_upload_success: {effective_url}")
			except Exception as exc:
				if self._logger:
					self._logger.surface_error(f"firebase_upload_failed: {exc}")
				# Continue anyway, the Android app will guard against the invalid URL

		msg_id = _uuid.uuid4().hex[:8]
		timestamp = int(time.time() * 1000)
		data: dict = {
			"sender": sender,
			"message_type": message_type,
			"content": content,
			"timestamp": timestamp,
			"format": format,
		}
		if request_id is not None:
			data["request_id"] = request_id
		if effective_url is not None:
			data["url"] = effective_url
		if effective_filename is not None:
			data["filename"] = effective_filename
		if suggestions:
			data["suggestions"] = suggestions

		await self._write_message_node(channel_id, msg_id, data)

		# Hide-aware FCM gating
		if message_type == "human":
			# Never push FCM for the human's own reply (existing behaviour).
			return None, msg_id

		hidden = await self._read_hidden(channel_id)
		if hidden and message_type != "question":
			# Suppressed: message stored in Firebase, no push.
			return None, msg_id
		if hidden and message_type == "question":
			# Auto-unhide atomically before pushing the notification.
			await self._write_hidden(channel_id, False)

		fcm_data: dict = {
			"channel_id": channel_id,
			"sb_message_type": message_type,
		}
		if message_type == "question" and request_id is not None:
			fcm_data["request_id"] = request_id

		try:
			await self._send_fcm(channel_id, message_type, sender, content, fcm_data)
		except Exception as exc:
			if self._logger:
				self._logger.surface_error(f"firebase_fcm_error: {exc}")

		if message_type == "question":
			return f"firebase_{request_id}", msg_id
		return None, msg_id

	async def _write_away_mode_node(self, active: bool, updated_at: int) -> None:
		def _write():
			db.reference("away_mode").set({"active": active, "updated_at": updated_at})

		await self._loop.run_in_executor(None, _write)

	async def write_away_mode_mirror(self, active: bool) -> None:
		await self._write_away_mode_node(active, int(time.time() * 1000))

	async def _read_hidden(self, channel_id: str) -> bool:
		def _read():
			ref = self._session_ref.child(channel_id).child("hidden")
			val = ref.get()
			if val is None:
				# Backward-compat: legacy state="closed" is treated as hidden=true
				state = self._session_ref.child(channel_id).child("state").get()
				return state == "closed"
			return bool(val)

		return await self._loop.run_in_executor(None, _read)

	async def _write_hidden(self, channel_id: str, value: bool) -> None:
		await self._loop.run_in_executor(
			None,
			lambda: self._session_ref.child(channel_id).child("hidden").set(value),
		)

	async def _write_message_node(self, channel_id: str, msg_id: str, data: dict) -> None:
		await self._loop.run_in_executor(
			None,
			lambda: self._session_ref.child(channel_id).child("messages").child(msg_id).set(data),
		)

	async def _send_fcm(
		self,
		channel_id: str,
		message_type: str,
		sender: str,
		content: str,
		fcm_data: dict,
	) -> None:
		# Background-mode FCM auto-display routes by AndroidConfig.notification.channel_id
		# rather than going through onMessageReceived. Without this, background pushes
		# fall through to Android's default "Miscellaneous" channel.
		android_channel_id = {
			"question": "switchboard_questions",
			"document": "switchboard_documents",
		}.get(message_type, "switchboard_updates")
		android_cfg = messaging.AndroidConfig(
			notification=messaging.AndroidNotification(channel_id=android_channel_id),
		)
		if message_type == "question":
			notif = messaging.Notification(
				title=f"Question from {sender}",
				body=content[:100] + ("..." if len(content) > 100 else ""),
			)
			msg = messaging.Message(
				notification=notif, topic="questions", data=fcm_data, android=android_cfg,
			)
		else:
			notif = messaging.Notification(
				title=f"Update from {sender}",
				body=content[:100] + ("..." if len(content) > 100 else ""),
			)
			msg = messaging.Message(
				notification=notif, topic="notifications", data=fcm_data, android=android_cfg,
			)

		await self._loop.run_in_executor(None, lambda: messaging.send(msg))

	async def _upload_file(self, local_path: Path) -> str:
		if not self._storage_bucket:
			raise ValueError("Firebase Storage not configured (missing SWITCHBOARD_FIREBASE_STORAGE_BUCKET)")

		def _do_upload():
			bucket = storage.bucket(self._storage_bucket)
			blob_name = f"documents/{_uuid.uuid4().hex}/{local_path.name}"
			blob = bucket.blob(blob_name)
			blob.upload_from_filename(str(local_path))
			# Generate a signed URL valid for 7 days using V4 signing
			return blob.generate_signed_url(version="v4", expiration=7 * 24 * 60 * 60)

		return await self._loop.run_in_executor(None, _do_upload)

	async def _write_default_meta_if_missing(self, channel_id: str) -> None:
		def _check_and_set():
			ref = self._session_ref.child(channel_id).child("meta")
			if not ref.get():
				ref.set({
					"type": "single",
					"project_key": "discovered",
					"created_at": int(time.time() * 1000),
					"task": "Auto-discovered session",
				})

		await self._loop.run_in_executor(None, _check_and_set)

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
		response_text: str | None = None,
	) -> None:
		# If we have the original msg_id (passed as part of correlation or via some other lookup)
		# we should write the response_text back to the message.
		# Since the gateway now passes response_text, we can at least mark it.
		# But wait, send_resolution_confirmation signature in MessengerBackend only has request_id, channel_id, correlation.
		# Registry.resolve_by_correlation should give us the msg_id.
		pass

	async def write_response_text(self, channel_id: str, msg_id: str, text: str) -> None:
		await self._loop.run_in_executor(
			None,
			lambda: self._session_ref.child(channel_id).child("messages").child(msg_id).child("response_text").set(text)
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

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
		msg = f"Spawned: `{channel_id}`"
		if prompt:
			msg += f"\n{prompt[:80]}"
		await self.write_channel_message(channel_id, "system", "notify", msg, format="markdown")

	async def send_text(self, text: str) -> None:
		# Use 'switchboard' as the default administrative channel for global errors/notifies
		await self.write_channel_message("switchboard", "system", "notify", text)

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
