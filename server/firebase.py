"""Firebase backend implementation."""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from pathlib import Path
from typing import AsyncIterator

import firebase_admin
from firebase_admin import credentials, db, messaging, storage

from server.gateway.bg_tasks import _spawn_bg
from server.logging_jsonl import JsonlLogger
from server.messenger import (
	Backend,
	MessageWriter,
	ResponsePoller,
	AwayModeMirror,
	ChannelLifecycle,
	InjectPort,
	IncomingResponse,
	CorrelationToken,
)


async def _no_op_async_logger(_message: str) -> None:
	"""Fallback when no JsonlLogger is configured. SupervisedListener and
	LoopSupervisor both require an async error_logger; this satisfies the
	type without doing anything."""
	return None


def _increment(n: int) -> object:
	"""Wrapper for atomic Firebase RTDB increment. Returns the documented
	server-value sentinel that the RTDB backend interprets as an atomic
	add-n operation. Centralized so tests can monkeypatch it.

	firebase-admin 7.x for Python does not expose ServerValue.increment as a
	helper — the underlying wire format is {".sv": {"increment": n}}, which
	is what the JS/Java SDK helpers produce. We emit that sentinel directly."""
	return {".sv": {"increment": n}}


class FirebaseBackend(
	MessageWriter,
	ResponsePoller,
	AwayModeMirror,
	ChannelLifecycle,
	InjectPort,
	Backend,
):
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
		# SupervisedListener instances, keyed by listener name. Populated by
		# the start_*/poll_* methods that own each listener. aclose() iterates
		# this map to stop everything cleanly on shutdown.
		from server.firebase_supervisor import SupervisedListener  # type: ignore
		self._supervised: dict[str, SupervisedListener] = {}
		self._away_mode_cmd_queue: asyncio.Queue[dict] = asyncio.Queue()
		self._away_mode_cmd_listener = None
		self._spawn_decision_future: asyncio.Future[dict] | None = None
		self._spawn_decision_listener = None

	def _on_response(self, event):
		if event.event_type == 'put' and event.data:
			path = event.path.strip('/')
			if not path:
				if isinstance(event.data, dict):
					for slot, data in event.data.items():
						if isinstance(data, dict) and 'text' in data:
							self._loop.call_soon_threadsafe(
								self._enqueue_response, slot, data
							)
						elif self._logger:
							self._loop.call_soon_threadsafe(
								lambda: _spawn_bg(self._logger.surface_error(f"firebase_malformed_response_entry: {slot} -> {type(data)}"), label="firebase_malformed_response_entry")
							)
				else:
					if self._logger:
						self._loop.call_soon_threadsafe(
							lambda: _spawn_bg(self._logger.surface_error(f"firebase_malformed_response_root: {type(event.data)}"), label="firebase_malformed_response_root")
						)
			elif path:
				if isinstance(event.data, dict) and 'text' in event.data:
					self._loop.call_soon_threadsafe(
						self._enqueue_response, path, event.data
					)
				else:
					if self._logger:
						self._loop.call_soon_threadsafe(
							lambda: _spawn_bg(self._logger.surface_error(f"firebase_malformed_response_path: {path} -> {type(event.data)}"), label="firebase_malformed_response_path")
						)

	def _enqueue_response(self, slot: str, data: dict):
		"""Route a response payload to the in-process queue.

		The phone keys responses by request_id (8-char hex, no '__' separator),
		so the slot itself is no longer routable. Routing fields are written into
		the payload by the phone: `cwd_key` and `sender`. We fall back to parsing
		the slot only for legacy `<cwd_key>__<sender>` slots that may still be
		on disk from older clients.
		"""
		from server.canonicalization import from_firebase_key
		text = data.get('text')
		if not isinstance(text, str):
			return
		cwd_key = data.get('cwd_key') if isinstance(data.get('cwd_key'), str) else None
		sender = data.get('sender') if isinstance(data.get('sender'), str) else None
		if not (cwd_key and sender):
			# Legacy fallback: composite slot form `<cwd_key>__<sender>`.
			parsed_cwd_key, sep, parsed_sender = slot.rpartition("__")
			if not sep or not parsed_cwd_key or not parsed_sender:
				if self._logger:
					_spawn_bg(
						self._logger.surface_error(
							f"firebase_response_unroutable: slot={slot!r} keys={list(data.keys())}"
						),
						label="firebase_response_unroutable",
					)
				return
			cwd_key = parsed_cwd_key
			sender = parsed_sender
		cwd = from_firebase_key(cwd_key)
		resp = IncomingResponse(correlation=(cwd, sender), text=text, slot=slot)
		_spawn_bg(self._response_queue.put(resp), label=f"response_enqueue:{cwd}:{sender}")

	def _on_command(self, event):
		if event.event_type == 'put' and event.data:
			path = event.path.strip('/')
			if not path:
				if isinstance(event.data, dict):
					for cmd_id, text in event.data.items():
						if isinstance(text, str):
							self._loop.call_soon_threadsafe(self._enqueue_command, cmd_id, text)
						elif self._logger:
							self._loop.call_soon_threadsafe(
								lambda: _spawn_bg(self._logger.surface_error(f"firebase_malformed_command_entry: {cmd_id} -> {type(text)}"), label="firebase_malformed_command_entry")
							)
				elif self._logger:
					self._loop.call_soon_threadsafe(
						lambda: _spawn_bg(self._logger.surface_error(f"firebase_malformed_command_root: {type(event.data)}"), label="firebase_malformed_command_root")
					)
			elif path:
				if isinstance(event.data, str):
					self._loop.call_soon_threadsafe(self._enqueue_command, path, event.data)
				elif self._logger:
					self._loop.call_soon_threadsafe(
						lambda: _spawn_bg(self._logger.surface_error(f"firebase_malformed_command_path: {path} -> {type(event.data)}"), label="firebase_malformed_command_path")
					)

	def _enqueue_command(self, command_id: str, text: str):
		_spawn_bg(self._command_queue.put(text), label=f"command_enqueue:{command_id}")
		def _cleanup():
			self._cmd_ref.child(command_id).delete()
		self._loop.run_in_executor(None, _cleanup)

	async def aclose(self) -> None:
		# Stop supervised listeners first so their watchdogs don't try to
		# reconnect while we're tearing the backend down.
		for sup in list(self._supervised.values()):
			try:
				await sup.stop()
			except Exception as exc:
				if self._logger:
					await self._logger.surface_error(
						f"supervisor_stop_failed: {sup.name} {exc}"
					)
		self._supervised.clear()

		# Legacy bare ListenerRegistration paths (poll_responses, poll_commands,
		# etc.) still use this until those tasks are migrated. After Tasks 4-7
		# this branch becomes a no-op.
		listeners = []
		for attr in (
			"_resp_listener", "_cmd_listener", "_away_mode_cmd_listener",
			"_spawn_decision_listener",
		):
			lst = getattr(self, attr)
			if lst:
				listeners.append(lst)
		listeners.extend(self._inject_listeners.values())
		self._inject_listeners.clear()
		if listeners:
			await asyncio.gather(*(
				asyncio.to_thread(l.close) for l in listeners
			), return_exceptions=True)

	def listener_health(self) -> list:
		"""Return per-listener health snapshots for /healthz.

		Returns a list of dicts (one per supervised listener) with name,
		state, last_event_at_seconds_ago, crash_count, last_crash_at.
		Timestamps are converted to "seconds ago" relative to time.monotonic()
		so the route response is a stable, JSON-friendly shape."""
		import time
		now = time.monotonic()
		out: list = []
		for sup in self._supervised.values():
			h = sup.health()
			out.append({
				"name": h.name,
				"state": h.state,
				"last_event_seconds_ago": (
					(now - h.last_event_at) if h.last_event_at is not None else None
				),
				"crash_count": h.crash_count,
				"last_crash_seconds_ago": (
					(now - h.last_crash_at) if h.last_crash_at is not None else None
				),
			})
		# Stable ordering for diffing.
		out.sort(key=lambda d: d["name"])
		return out

	async def write_channel_message(
		self,
		cwd: str,
		sender: str,
		message_type: str,
		content: str,
		*,
		request_id: str | None = None,
		title: str | None = None,
		url: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
		filename: str | None = None,
		rejected: bool = False,
		attached_to_msg_id: str | None = None,
	) -> tuple[CorrelationToken | None, str | None]:
		from server.canonicalization import to_firebase_key
		from datetime import datetime, timezone

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
					await self._logger.info(f"firebase_upload_success: {effective_url}")
			except Exception as exc:
				if self._logger:
					await self._logger.surface_error(f"firebase_upload_failed: {exc}")

		key = to_firebase_key(cwd)
		now = datetime.now(timezone.utc).isoformat()
		ref = db.reference(f'channels/{key}/messages').push()
		msg_id = ref.key
		payload = {
			"type": message_type,
			"sender": sender,
			"text": content,
			"format": format,
			"timestamp": now,
			"cancelled": False,
			"rejected": rejected,
		}
		if title is not None:
			payload["title"] = title[:80]
		if request_id:
			payload["request_id"] = request_id
		if effective_url:
			payload["url"] = effective_url
		if effective_filename:
			payload["filename"] = effective_filename
		if suggestions:
			payload["suggestions"] = list(suggestions)
		if attached_to_msg_id:
			payload["attached_to_msg_id"] = attached_to_msg_id

		if self._logger:
			await self._logger.info(f"firebase_write_message: {key}/{msg_id}")

		# Auto-unhide on question writes only
		if message_type == "question":
			await asyncio.to_thread(lambda: db.reference(f'channels/{key}/hidden').set(False))

		await asyncio.to_thread(lambda: ref.set(payload))

		# cwd_canonical for popover display
		await asyncio.to_thread(lambda: db.reference(f'channels/{key}/cwd_canonical').set(cwd))

		# Channel-level title only when non-empty
		if title:
			await asyncio.to_thread(lambda: db.reference(f'channels/{key}/title').set(title[:80]))

		# last_activity_at + preview
		preview = content[:120].replace("\n", " ").strip()
		await asyncio.to_thread(lambda: db.reference(f'channels/{key}').update({
			'last_activity_at': now,
			'preview': preview,
		}))

		# unread_count: atomic increment for every non-Human message
		if message_type != "human":
			await asyncio.to_thread(
				lambda: db.reference(f'channels/{key}/unread_count').set(_increment(1))
			)

		# pending_responses is owned by the Registry mirror callback (see
		# make_pending_mirror_writer): registry.add fires +1, registry.resolve /
		# remove / cancel-on-spawn / cancel_pending_for_cwd fire -1. Doing it here
		# AND there double-counts every question by exactly +1, drifting the
		# counter upward over time.

		# FCM notification
		if message_type != "human":
			fcm_data: dict = {
				"channel_key": key,
				"sb_message_type": message_type,
				"message_id": msg_id,
			}
			if message_type == "question" and request_id is not None:
				fcm_data["request_id"] = request_id
			try:
				await self._send_fcm(key, message_type, sender, content, fcm_data)
			except Exception as exc:
				if self._logger:
					await self._logger.surface_error(f"firebase_fcm_error: {exc}")

		# Correlation: encode (cwd, sender) so the dispatch loop can route by key
		correlation = (cwd, sender)
		return correlation, msg_id

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		from server.canonicalization import to_firebase_key
		key = to_firebase_key(cwd)
		msgs_ref = db.reference(f'channels/{key}/messages')
		def _walk():
			snapshot = msgs_ref.get()
			if not isinstance(snapshot, dict):
				return None
			for msg_id, payload in snapshot.items():
				if isinstance(payload, dict) and payload.get("request_id") == request_id:
					return msg_id
			return None
		msg_id = await asyncio.to_thread(_walk)
		if msg_id is not None:
			await asyncio.to_thread(
				lambda: msgs_ref.child(msg_id).child("cancelled").set(True)
			)

	async def send_stale_reply_notice(self, cwd: str, sender: str) -> None:
		await self.write_channel_message(
			cwd, "system", "system",
			f"Reply for {sender} couldn't be delivered — the question was withdrawn.",
			format="plain",
			rejected=True,
		)

	async def has_messages(self, cwd: str) -> bool:
		from server.canonicalization import to_firebase_key
		key = to_firebase_key(cwd)
		def _check():
			ref = db.reference(f'channels/{key}/messages')
			snapshot = ref.get(shallow=True)
			return bool(snapshot)
		return await asyncio.to_thread(_check)

	async def read_channel_meta(self, cwd: str) -> dict:
		from server.canonicalization import to_firebase_key
		key = to_firebase_key(cwd)
		def _read():
			ref = db.reference(f'channels/{key}')
			snapshot = ref.get() or {}
			return {
				"title": snapshot.get("title"),
				"last_activity_at": snapshot.get("last_activity_at"),
				"hidden": bool(snapshot.get("hidden", False)),
			}
		return await asyncio.to_thread(_read)

	async def write_spawn_collision_prompt(self, spawn_id, cwd, channel_title, last_activity_at, hidden):
		from server.canonicalization import to_firebase_key
		from datetime import datetime, timezone
		def _write():
			db.reference(f'spawn_collisions/{spawn_id}').set({
				'cwd': cwd,
				'cwd_key': to_firebase_key(cwd),
				'channel_title': channel_title,
				'last_activity_at': last_activity_at,
				'hidden': hidden,
				'pushed_at': datetime.now(timezone.utc).isoformat(),
			})
		await asyncio.to_thread(_write)

	async def clear_spawn_collision_prompt(self, spawn_id):
		def _clear():
			db.reference(f'spawn_collisions/{spawn_id}').delete()
		await asyncio.to_thread(_clear)

	async def wipe_channel(self, cwd: str) -> None:
		from server.canonicalization import to_firebase_key
		key = to_firebase_key(cwd)
		def _wipe():
			db.reference(f'channels/{key}/messages').delete()
			db.reference(f'channels/{key}/title').delete()
			db.reference(f'channels/{key}/preview').delete()
			db.reference(f'channels/{key}/last_activity_at').delete()
			db.reference(f'channels/{key}/unread_count').delete()
			db.reference(f'channels/{key}/pending_responses').delete()
			db.reference(f'channels/{key}/away_mode').delete()
			responses_ref = db.reference('responses')
			all_responses = responses_ref.get(shallow=True) or {}
			for slot in list(all_responses.keys()):
				if slot.startswith(f"{key}__"):
					responses_ref.child(slot).delete()
		await asyncio.to_thread(_wipe)

	async def set_channel_hidden(self, cwd: str, hidden: bool) -> None:
		from server.canonicalization import to_firebase_key
		key = to_firebase_key(cwd)
		await asyncio.to_thread(lambda: db.reference(f'channels/{key}/hidden').set(hidden))

	async def write_away_mode_mirror(self, cwd: str | None, active: bool | None) -> None:
		from server.canonicalization import to_firebase_key
		if cwd is None:
			await asyncio.to_thread(lambda: db.reference('global_settings/away_mode').set(active))
			return
		key = to_firebase_key(cwd)
		ref_path = f'channels/{key}/away_mode'
		if active is None:
			await asyncio.to_thread(lambda: db.reference(ref_path).delete())
		else:
			await asyncio.to_thread(lambda: db.reference(ref_path).set(active))

	async def load_away_mode_snapshot(self, registry) -> None:
		from server.canonicalization import from_firebase_key
		def _read():
			global_val = db.reference('global_settings/away_mode').get()
			channels = db.reference('channels').get(shallow=False) or {}
			return global_val, channels
		global_val, channels = await asyncio.to_thread(_read)
		registry.update_global_away_cache(bool(global_val))
		if not isinstance(channels, dict):
			return
		for key, channel in channels.items():
			if not isinstance(channel, dict):
				continue
			away = channel.get('away_mode')
			if away is None:
				continue
			try:
				cwd = from_firebase_key(key)
			except Exception:
				continue
			registry.update_cwd_override_cache(cwd, bool(away))

	async def start_away_mode_listeners(self, registry) -> None:
		from server.canonicalization import from_firebase_key
		from server.firebase_supervisor import SupervisedListener

		def _on_global(event):
			active = bool(event.data) if event.data is not None else False
			try:
				self._loop.call_soon_threadsafe(registry.update_global_away_cache, active)
			except Exception as exc:
				if self._logger:
					asyncio.run_coroutine_threadsafe(
						self._logger.surface_error(f"away_mode_global listener error: {exc}"),
						self._loop,
					)

		def _on_channel(event):
			if not event.path or event.path == "/":
				return
			parts = event.path.strip("/").split("/")
			if len(parts) == 2 and parts[1] == "away_mode":
				cwd = from_firebase_key(parts[0])
				active = None if event.data is None else bool(event.data)
				try:
					self._loop.call_soon_threadsafe(registry.update_cwd_override_cache, cwd, active)
				except Exception as exc:
					if self._logger:
						asyncio.run_coroutine_threadsafe(
							self._logger.surface_error(
								f"away_mode_channel listener error ({cwd!r}): {exc}"
							),
							self._loop,
						)

		err = self._logger.surface_error if self._logger else _no_op_async_logger
		self._supervised["away_mode_global"] = SupervisedListener(
			name="away_mode_global",
			path="global_settings/away_mode",
			callback=_on_global,
			error_logger=err,
			loop=self._loop,
		)
		self._supervised["away_mode_channels"] = SupervisedListener(
			name="away_mode_channels",
			path="channels",
			callback=_on_channel,
			error_logger=err,
			loop=self._loop,
		)
		self._supervised["away_mode_global"].start()
		self._supervised["away_mode_channels"].start()

	async def reset_all_pending_responses(self) -> None:
		def _reset():
			channels = db.reference('channels').get(shallow=True) or {}
			if not isinstance(channels, dict) or not channels:
				return
			updates = {f'channels/{k}/pending_responses': 0 for k in channels.keys()}
			db.reference().update(updates)
		await asyncio.to_thread(_reset)

	async def reset_all_away_mode(self) -> None:
		"""Force away mode off globally and clear every per-channel override.

		Called once on server startup. Rationale: in stateful HTTP mode, a server
		restart invalidates every active CC session — those agents lose access to
		the switchboard MCP tools (issue #27142) and can no longer call
		ask_human / notify_human / etc. If we left away_mode=true on restart,
		the Stop hook would block their turn-end with "call ask_human" but the
		tool isn't available, producing a useless loop until the user manually
		`/exit`s and relaunches CC.

		Resetting away mode on startup means pre-restart agents fall back to
		normal terminal output (which they can do without switchboard tools).
		The user re-enables away mode by spawning new agents (the spawn handler
		sets the new channel's away_mode override) or via the phone toggle.
		"""
		def _reset():
			# Walk channels, queue an explicit None write for any away_mode field
			# that is currently set. Multi-path update is atomic from Firebase's
			# perspective — listeners see one event per path.
			channels = db.reference('channels').get(shallow=False) or {}
			updates: dict[str, object] = {'global_settings/away_mode': False}
			if isinstance(channels, dict):
				for key, channel in channels.items():
					if isinstance(channel, dict) and channel.get('away_mode') is not None:
						updates[f'channels/{key}/away_mode'] = None
			db.reference().update(updates)
		await asyncio.to_thread(_reset)

	async def delete_legacy_away_mode_node(self) -> None:
		"""One-shot startup migration: delete the old away_mode/ top-level node.
		Idempotent — if the node doesn't exist, this is a no-op."""
		def _do():
			ref = db.reference('away_mode')
			if ref.get(shallow=True) is not None:
				ref.delete()
		await asyncio.to_thread(_do)

	def make_pending_mirror_writer(self):
		"""Returns a sync callable (cwd, delta) that schedules an atomic
		pending_responses Firebase increment for the cwd. The callable is meant
		for Registry.set_pending_mirror()."""
		from server.canonicalization import to_firebase_key
		import logging as _logging
		def _write(cwd: str, delta: int) -> None:
			key = to_firebase_key(cwd)
			def _do():
				db.reference(f'channels/{key}/pending_responses').set(_increment(delta))
			try:
				_spawn_bg(asyncio.to_thread(_do), label=f"pending_mirror:{key}:{delta:+d}")
			except RuntimeError:
				# No running loop — common in synchronous unit tests where Registry
				# mutations happen outside an event loop. Production paths always
				# have a loop; an exception here in production is a bug worth seeing.
				_logging.getLogger(__name__).debug(
					"pending_mirror skipped: no running event loop (cwd=%s, delta=%d)",
					cwd, delta,
				)
		return _write

	async def _send_fcm(
		self,
		channel_key: str,
		message_type: str,
		sender: str,
		content: str,
		fcm_data: dict,
	) -> None:
		# Data-only FCM messages: onMessageReceived runs on the client in foreground,
		# background, AND killed states, so the client always controls the PendingIntent
		# and its extras. Any android.notification field (even just channel_id) flips
		# FCM into "notification message" mode and lets Android render the tray entry
		# itself, bypassing our service — so we do NOT set AndroidNotification here.
		android_cfg = messaging.AndroidConfig(
			priority="high",  # data-only requires high to avoid doze deferral
		)
		title = (
			f"Question from {sender}" if message_type == "question"
			else f"Update from {sender}"
		)
		body = content[:100] + ("..." if len(content) > 100 else "")
		fcm_data = {**fcm_data, "title": title, "body": body}
		topic = "questions" if message_type == "question" else "notifications"
		msg = messaging.Message(topic=topic, data=fcm_data, android=android_cfg)

		await asyncio.to_thread(lambda: messaging.send(msg))

	async def _upload_file(self, local_path: Path) -> str:
		if not self._storage_bucket:
			raise ValueError("Firebase Storage not configured (missing SWITCHBOARD_FIREBASE_STORAGE_BUCKET)")

		def _do_upload():
			bucket = storage.bucket(self._storage_bucket)
			blob_name = f"documents/{_uuid.uuid4().hex}/{local_path.name}"
			blob = bucket.blob(blob_name)
			blob.upload_from_filename(str(local_path))
			return blob.generate_signed_url(version="v4", expiration=7 * 24 * 60 * 60)

		return await asyncio.to_thread(_do_upload)

	async def send_timeout_followup(
		self,
		request_id: str,
		channel_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		try:
			await self.write_channel_message(
				channel_id, "system", "notify",
				f"Question timed out after {timeout_seconds}s (request: {request_id})",
			)
		except Exception as exc:
			if self._logger:
				await self._logger.surface_error(f"firebase_timeout_notify_error: {exc}")

	async def send_resolution_confirmation(
		self,
		request_id: str,
		channel_id: str,
		correlation: CorrelationToken,
		response_text: str | None = None,
	) -> None:
		from server.canonicalization import to_firebase_key
		# The phone keys responses by request_id; older clients used the composite
		# `<cwd_key>__<sender>` form. Delete both candidates so cleanup is robust
		# regardless of which key shape produced this response.
		def _cleanup():
			self._resp_ref.child(request_id).delete()
			if isinstance(correlation, tuple) and len(correlation) == 2:
				cwd, sender = correlation
				legacy_slot = f"{to_firebase_key(cwd)}__{sender}"
				self._resp_ref.child(legacy_slot).delete()
		await self._loop.run_in_executor(None, _cleanup)

	async def delete_response_slot(self, slot: str) -> None:
		def _delete():
			self._resp_ref.child(slot).delete()
		await self._loop.run_in_executor(None, _delete)

	def _on_away_mode_command(self, event):
		if event.event_type != 'put' or not event.data:
			return
		path = event.path.strip('/')
		data = event.data
		if not path and isinstance(data, dict):
			for cmd_id, entry in data.items():
				if isinstance(entry, dict) and 'type' in entry:
					self._loop.call_soon_threadsafe(
						self._enqueue_away_mode_cmd, cmd_id, entry
					)
		elif path and isinstance(data, dict) and 'type' in data:
			self._loop.call_soon_threadsafe(
				self._enqueue_away_mode_cmd, path, data
			)

	def _enqueue_away_mode_cmd(self, cmd_id: str, entry: dict):
		_spawn_bg(self._away_mode_cmd_queue.put(entry), label=f"away_mode_cmd_enqueue:{cmd_id}")
		def _cleanup():
			db.reference(f'away_mode_commands/{cmd_id}').delete()
		self._loop.run_in_executor(None, _cleanup)

	async def poll_away_mode_commands(self) -> AsyncIterator[dict]:
		from server.firebase_supervisor import SupervisedListener
		if "away_mode_commands" not in self._supervised:
			err = self._logger.surface_error if self._logger else _no_op_async_logger
			sup = SupervisedListener(
				name="away_mode_commands",
				path="away_mode_commands",
				callback=self._on_away_mode_command,
				error_logger=err,
				loop=self._loop,
			)
			self._supervised["away_mode_commands"] = sup
			sup.start()
		while True:
			yield await self._away_mode_cmd_queue.get()

	def _on_spawn_decision(self, event):
		# Resolves on the first 'decision' write under spawn_collisions/{spawn_id}.
		# The dialog write itself is also a 'put' event (path=''), so guard on the
		# decision payload shape rather than path alone.
		if event.event_type != 'put' or not event.data:
			return
		path = event.path.strip('/')
		data = event.data
		decision = None
		if path == 'decision' and isinstance(data, dict) and 'action' in data:
			decision = data
		elif not path and isinstance(data, dict):
			# Root write — extract decision child if it landed in the same payload.
			child = data.get('decision')
			if isinstance(child, dict) and 'action' in child:
				decision = child
		if decision is not None and self._spawn_decision_future and not self._spawn_decision_future.done():
			self._loop.call_soon_threadsafe(self._spawn_decision_future.set_result, decision)

	async def poll_spawn_collision_decision(self, spawn_id: str) -> dict:
		self._spawn_decision_future = asyncio.get_running_loop().create_future()
		if self._spawn_decision_listener:
			# Fire-and-forget; the SSE teardown can stall the event loop for many seconds.
			self._loop.run_in_executor(None, self._spawn_decision_listener.close)
		self._spawn_decision_listener = db.reference(f'spawn_collisions/{spawn_id}').listen(
			self._on_spawn_decision
		)
		try:
			return await self._spawn_decision_future
		finally:
			if self._spawn_decision_listener:
				listener = self._spawn_decision_listener
				self._spawn_decision_listener = None
				# listener.close() blocks for many seconds while the SSE stream tears down.
				# Run it in the executor so the dispatch loop can immediately proceed to the
				# clear_spawn_collision_prompt call and dismiss the phone-side dialog.
				self._loop.run_in_executor(None, listener.close)
			self._spawn_decision_future = None

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		from server.firebase_supervisor import SupervisedListener
		if "responses" not in self._supervised:
			err = self._logger.surface_error if self._logger else _no_op_async_logger
			sup = SupervisedListener(
				name="responses",
				path="responses",
				callback=self._on_response,
				error_logger=err,
				loop=self._loop,
			)
			self._supervised["responses"] = sup
			sup.start()
		# Drop the legacy bare-registration field — it's never set now.
		while True:
			yield await self._response_queue.get()

	async def poll_commands(self) -> AsyncIterator[str]:
		from server.firebase_supervisor import SupervisedListener
		if "commands" not in self._supervised:
			err = self._logger.surface_error if self._logger else _no_op_async_logger
			sup = SupervisedListener(
				name="commands",
				path="commands",
				callback=self._on_command,
				error_logger=err,
				loop=self._loop,
			)
			self._supervised["commands"] = sup
			sup.start()
		while True:
			yield await self._command_queue.get()

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
		msg = f"Spawned: `{channel_id}`"
		if prompt:
			msg += f"\n{prompt[:80]}"
		await self.write_channel_message(channel_id, "system", "notify", msg, format="markdown")

	async def send_text(self, text: str) -> None:
		# Admin/system channel for global errors/notifies that have no natural cwd.
		# Leading underscore can't appear in any canonical cwd (which start with a drive
		# letter), so this key never collides with a user-named workspace channel.
		await self.write_channel_message("_admin", "system", "notify", text, title="Switchboard System")

	async def write_session_meta(
		self,
		channel_id: str,
		type: str,
		project_key: str,
		*,
		agent_senders: list[str] | None = None,
		task: str | None = None,
	) -> None:
		from server.canonicalization import to_firebase_key
		from datetime import datetime, timezone
		key = to_firebase_key(channel_id)
		meta: dict = {
			"type": type,
			"project_key": project_key,
			"created_at": datetime.now(timezone.utc).isoformat(),
		}
		if agent_senders is not None:
			meta["agent_senders"] = agent_senders
		if task is not None:
			meta["task"] = task
		await asyncio.to_thread(
			lambda: db.reference(f'channels/{key}/meta').set(meta),
		)

	async def start_inject_listener(self, session_id: str) -> None:
		from server.canonicalization import to_firebase_key
		from server.firebase_supervisor import SupervisedListener

		key = to_firebase_key(session_id)
		listener_name = f"inject:{session_id}"
		if listener_name in self._supervised:
			return  # already running for this session

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

		err = self._logger.surface_error if self._logger else _no_op_async_logger
		sup = SupervisedListener(
			name=listener_name,
			path=f"channels/{key}/inject_queue",
			callback=_on_inject,
			error_logger=err,
			loop=self._loop,
		)
		self._supervised[listener_name] = sup
		sup.start()

	async def poll_inject_messages(self):
		while True:
			yield await self._inject_queue_internal.get()
