"""Firebase backend implementation."""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from pathlib import Path
from typing import AsyncIterator

import firebase_admin
from firebase_admin import credentials, db, messaging, storage

from server.command_freshness import COMMAND_TTL_SECONDS, command_age_seconds
from server.gateway.bg_tasks import _spawn_bg
from server.logging_jsonl import JsonlLogger
from server.messenger import (
	Backend,
	MessageWriter,
	ResponsePoller,
	AwayModeMirror,
	ConversationStore,
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


def _member_payload(member) -> dict:
	"""The members_active node shape (write_conversation_member and
	move_conversation_member write the identical dict)."""
	return {
		"cli_session_id": member.cli_session_id,
		"sender": member.sender,
		"cwd": member.cwd,
		"surface": member.surface,
		"alive": member.alive,
		"session_lost_permanently": member.session_lost_permanently,
		"session_ended_at": member.session_ended_at,
		"session_end_reason": member.session_end_reason,
		"joined_at": member.joined_at,
		"last_seen_seq": member.last_seen_seq,
	}


class FirebaseBackend(
	MessageWriter,
	ResponsePoller,
	AwayModeMirror,
	ConversationStore,
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
		self._database_url = database_url
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

		self._response_queue: asyncio.Queue[IncomingResponse] = asyncio.Queue()
		self._loop = asyncio.get_running_loop()
		# SupervisedListener instances, keyed by listener name. Populated by
		# the start_*/poll_* methods that own each listener. aclose() iterates
		# this map to stop everything cleanly on shutdown.
		from server.firebase_supervisor import SupervisedListener  # type: ignore
		self._supervised: dict[str, SupervisedListener] = {}
		self._away_mode_cmd_queue: asyncio.Queue[dict] = asyncio.Queue()
		self._status_request_queue: asyncio.Queue[dict] = asyncio.Queue()

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

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		# Messages live at /messages/<conv_id>.
		conv_id = conversation_id
		msgs_ref = db.reference(f"messages/{conv_id}")
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
		# Cancellation is a resolution — clear the pending_questions record too.
		try:
			await self.remove_pending_question_record(conv_id, request_id)
		except Exception:
			pass  # best-effort cleanup; don't mask the cancel itself

	async def add_pending_question_record(
		self,
		conversation_id: str,
		request_id: str,
		*,
		sender: str,
		msg_id: str | None,
		question_text: str,
		suggestions: list[str] | None = None,
		cli_session_id: str | None = None,
		asked_at: str | None = None,
	) -> None:
		"""Persist an in-flight ask_human at
		/conversations/<id>/pending_questions/<request_id> per the 2026-05-19 spec."""
		ref = db.reference(f"conversations/{conversation_id}/pending_questions/{request_id}")
		await asyncio.to_thread(ref.set, {
			"sender": sender,
			"questionText": question_text,
			"cancelled": False,
			"msgId": msg_id,
			"suggestions": suggestions,
			"cliSessionId": cli_session_id,
			"askedAt": asked_at,
		})

	async def remove_pending_question_record(
		self,
		conversation_id: str,
		request_id: str,
	) -> None:
		ref = db.reference(f"conversations/{conversation_id}/pending_questions/{request_id}")
		await asyncio.to_thread(ref.delete)

	async def write_agent_status(
		self,
		conv_id: str,
		sender: str,
		state: str,
		detail: str | None,
	) -> None:
		ref = db.reference(f'conversations/{conv_id}/agent_status/{sender}')
		if state == "clear":
			await asyncio.to_thread(ref.delete)
			return
		payload = {
			"state": state,
			"detail": detail,
			"updated_at": {".sv": "timestamp"},  # Firebase server timestamp sentinel
		}
		await asyncio.to_thread(lambda: ref.set(payload))

	async def send_stale_reply_notice(self, conversation_id: str, sender: str) -> None:
		await self.write_conversation_message(
			conversation_id, "system", "system",
			f"Reply for {sender} couldn't be delivered — the question was withdrawn.",
			format="plain",
			rejected=True,
		)

	async def load_away_mode_snapshot(self, registry) -> None:
		def _read():
			global_val = db.reference('global_settings/away_mode').get()
			return global_val
		global_val = await asyncio.to_thread(_read)
		registry.update_global_away_cache(bool(global_val))

	async def start_away_mode_listeners(self, registry) -> None:
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

		err = self._logger.surface_error if self._logger else _no_op_async_logger
		self._supervised["away_mode_global"] = SupervisedListener(
			name="away_mode_global",
			path="global_settings/away_mode",
			callback=_on_global,
			error_logger=err,
			loop=self._loop,
		)
		self._supervised["away_mode_global"].start()
		# Per-channel away_mode overrides were retired in the conversations redesign.
		# The global flag (above) is the only away-mode signal the server now uses.

	async def reset_all_pending_responses(self) -> None:
		def _reset():
			conversations = db.reference('conversations').get(shallow=True) or {}
			if not isinstance(conversations, dict) or not conversations:
				return
			updates = {f'conversations/{conv_id}/pending_responses': 0 for conv_id in conversations.keys()}
			db.reference().update(updates)
		await asyncio.to_thread(_reset)

	async def list_conversation_ids(self) -> list[str]:
		def _get():
			snapshot = db.reference('conversations').get(shallow=True) or {}
			return list(snapshot.keys()) if isinstance(snapshot, dict) else []
		return await asyncio.to_thread(_get)

	async def get_conversation_meta(self, conv_id: str) -> dict | None:
		def _get():
			return db.reference(f"conversations/{conv_id}/meta").get()
		return await asyncio.to_thread(_get)

	async def delete_conversation_nodes(self, conv_id: str) -> None:
		"""Atomically delete a conversation's index card and its companion
		top-level /messages and /answers nodes (multi-location update)."""
		def _delete():
			db.reference().update({
				f"conversations/{conv_id}": None,
				f"messages/{conv_id}": None,
				f"answers/{conv_id}": None,
			})
		await asyncio.to_thread(_delete)

	async def reset_all_away_mode(self) -> None:
		"""Force away mode off globally.

		Called once on server startup. Rationale: in stateful HTTP mode, a server
		restart invalidates every active CC session — those agents lose access to
		the switchboard MCP tools (issue #27142) and can no longer call
		ask_human / notify_human / etc. If we left away_mode=true on restart,
		the Stop hook would block their turn-end with "call ask_human" but the
		tool isn't available, producing a useless loop until the user manually
		`/exit`s and relaunches CC.

		Resetting away mode on startup means pre-restart agents fall back to
		normal terminal output (which they can do without switchboard tools).
		The user re-enables away mode via the phone toggle.
		Clears /away_mode_commands in the same pass so stale queued toggles cannot replay after the reset (decided 2026-06-11).
		"""
		def _reset():
			db.reference('global_settings/away_mode').set(False)
			# Also drop any queued away commands from before the restart: this
			# reset is an authoritative state decision, so a stale enter_global
			# left behind by a crash must not replay from the command
			# listener's initial snapshot and silently re-enable away mode
			# (M06). The command listener attaches after startup runs this.
			db.reference('away_mode_commands').delete()
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
		"""Returns a sync callable (conversation_id, delta) that schedules an atomic
		pending_responses Firebase increment under /conversations/<conv_id>/pending_responses."""
		import logging as _logging
		def _write(conversation_id: str, delta: int) -> None:
			def _do():
				db.reference(f'conversations/{conversation_id}/pending_responses').set(_increment(delta))
			try:
				_spawn_bg(asyncio.to_thread(_do), label=f"pending_mirror:{conversation_id}:{delta:+d}")
			except RuntimeError:
				# No running loop — common in synchronous unit tests where Registry
				# mutations happen outside an event loop. Production paths always
				# have a loop; an exception here in production is a bug worth seeing.
				_logging.getLogger(__name__).debug(
					"pending_mirror skipped: no running event loop (conversation_id=%s, delta=%d)",
					conversation_id, delta,
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

	async def fetch_database_rules(self) -> str:
		"""Read the DEPLOYED RTDB rules via the REST management endpoint
		(<database_url>/.settings/rules.json), authenticated with the admin
		credential's OAuth token. Read-only; used by the startup rules audit
		(server/rules_audit.py, REV-004)."""
		def _do_fetch() -> str:
			import urllib.request
			app = firebase_admin.get_app()
			token = app.credential.get_access_token().access_token
			url = f"{self._database_url.rstrip('/')}/.settings/rules.json?access_token={token}"
			with urllib.request.urlopen(url, timeout=10) as resp:
				return resp.read().decode("utf-8")
		return await asyncio.to_thread(_do_fetch)

	async def read_document(self, conv_id: str, msg_id: str) -> tuple[bytes, str]:
		"""Return (bytes, filename) for a document message, downloading the blob via
		the Admin SDK. Raises LookupError if the message is missing, is not a
		document, or has no resolvable storage path. Raises ValueError if the blob
		exceeds _MAX_DOCUMENT_BYTES (guard fires before download)."""
		from server.gateway.document import _blob_path_from_url, _MAX_DOCUMENT_BYTES

		msg = await asyncio.to_thread(
			lambda: db.reference(f"messages/{conv_id}/{msg_id}").get()
		)
		if not isinstance(msg, dict) or msg.get("type") != "document":
			raise LookupError(f"no document message at {conv_id}/{msg_id}")

		filename = msg.get("filename") or "document"
		blob_path = msg.get("storage_path") or _blob_path_from_url(msg.get("url"))
		if not blob_path:
			raise LookupError(f"document message has no resolvable blob path: {conv_id}/{msg_id}")

		def _download():
			bucket = storage.bucket(self._storage_bucket)
			blob = bucket.blob(blob_path)
			blob.reload()  # populate metadata (size) before downloading
			if blob.size is not None and blob.size > _MAX_DOCUMENT_BYTES:
				raise ValueError(f"document exceeds max size ({blob.size} bytes, max {_MAX_DOCUMENT_BYTES})")
			return blob.download_as_bytes()

		data = await asyncio.to_thread(_download)
		return data, filename

	async def _upload_file(self, local_path: Path) -> tuple[str, str]:
		if not self._storage_bucket:
			raise ValueError("Firebase Storage not configured (missing SWITCHBOARD_FIREBASE_STORAGE_BUCKET)")

		def _do_upload():
			bucket = storage.bucket(self._storage_bucket)
			blob_name = f"documents/{_uuid.uuid4().hex}/{local_path.name}"
			blob = bucket.blob(blob_name)
			blob.upload_from_filename(str(local_path))
			url = blob.generate_signed_url(version="v4", expiration=7 * 24 * 60 * 60)
			return url, blob_name

		return await asyncio.to_thread(_do_upload)

	async def send_timeout_followup(
		self,
		request_id: str,
		conversation_id: str,
		timeout_seconds: int,
		correlation: CorrelationToken,
	) -> None:
		try:
			await self.write_conversation_message(
				conversation_id, "system", "notify",
				f"Question timed out after {timeout_seconds}s (request: {request_id})",
			)
		except Exception as exc:
			if self._logger:
				await self._logger.surface_error(f"firebase_timeout_notify_error: {exc}")

	async def delete_response_slot(self, slot: str) -> None:
		def _delete():
			# Slots are full RTDB paths under the top-level answers node
			# ("answers/<conv_id>/<request_id>"). Anything else is a bug
			# upstream; refuse rather than delete an arbitrary path.
			if slot.startswith("answers/"):
				db.reference(slot).delete()
		await self._loop.run_in_executor(None, _delete)

	async def poll_away_mode_commands(self) -> AsyncIterator[dict]:
		async def _enqueue(cmd: dict) -> None:
			await self._away_mode_cmd_queue.put(cmd)
		self._start_command_listener("away_mode_commands", _enqueue)
		while True:
			yield await self._away_mode_cmd_queue.get()

	async def poll_status_request_commands(self) -> AsyncIterator[dict]:
		async def _enqueue(cmd: dict) -> None:
			await self._status_request_queue.put(cmd)
		self._start_command_listener(
			"widget/status_request", _enqueue, name="status_request", stale_notice=False,
		)
		while True:
			yield await self._status_request_queue.get()

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		# Answers arrive via start_conversation_answers_listener, which
		# enqueues IncomingResponse onto _response_queue; this iterator just
		# drains the queue.
		while True:
			yield await self._response_queue.get()

	async def write_admin_notification(self, text: str) -> None:
		"""Push a system broadcast to /admin_notifications/<push_key>.
		Phone-side admin listener surfaces these in a dedicated synthetic channel."""
		from datetime import datetime, timezone
		now = datetime.now(timezone.utc).isoformat()
		ref = db.reference("admin_notifications").push()
		payload = {
			"sender": "system",
			"type": "notify",
			"text": text,
			"format": "markdown",
			"timestamp": now,
		}
		await asyncio.to_thread(ref.set, payload)

	async def send_text(self, text: str) -> None:
		# Admin/system channel for global errors/notifies that have no natural cwd.
		# Leading underscore can't appear in any canonical cwd (which start with a drive
		# letter), so this key never collides with a user-named workspace channel.
		await self.write_admin_notification(text)

	# -------------------------------------------------------------------------
	# New-schema node writers (Task 29)
	# -------------------------------------------------------------------------

	async def delete_open_conversation_node(self) -> None:
		"""One-shot chunk 5 cleanup: the open-marker RTDB node is retired; deleting
		it sends the phone's open-accent listener a permanent null."""
		await asyncio.to_thread(lambda: db.reference("global_settings/open_conversation_id").delete())

	async def set_global_away_mode(self, value: bool) -> None:
		"""Write the global away-mode flag to /global_settings/away_mode."""
		ref = db.reference("global_settings/away_mode")
		await asyncio.to_thread(ref.set, bool(value))

	async def set_global_wsl_available(self, available: bool) -> None:
		"""Write whether WSL is detected on this host to /global_settings/wsl_available."""
		ref = db.reference("global_settings/wsl_available")
		await asyncio.to_thread(ref.set, bool(available))

	async def write_widget_rings(self, rings: dict) -> None:
		"""Publish the per-session context rings map (keyed by Claude Code session_id).
		Always fanned out; an empty map clears the node."""
		await asyncio.to_thread(lambda: db.reference("widget/rings").set(rings or {}))

	async def write_widget_quota(self, quota: dict | None) -> None:
		"""Publish plan quota. firebase_admin rejects set(None), so a None quota
		clears the node via delete()."""
		ref = db.reference("widget/quota")
		if quota is None:
			await asyncio.to_thread(ref.delete)
		else:
			await asyncio.to_thread(lambda: ref.set(quota))

	async def write_widget_pushed_at(self, ts: str) -> None:
		"""Publish the last-push timestamp (staleness signal for readers)."""
		await asyncio.to_thread(lambda: db.reference("widget/pushed_at").set(ts))

	async def write_widget_status(self, status: dict) -> None:
		"""Publish the Claude service-status view (level, description, incidents,
		fetched_at, watch_state, button) for all surfaces to render."""
		await asyncio.to_thread(lambda: db.reference("widget/status").set(status))

	async def write_session_record(self, cli_session_id: str, payload: dict) -> None:
		"""Publish one session record under /sessions/<cli_session_id>. Ids are
		uuid-like and RTDB-safe as-is."""
		await asyncio.to_thread(lambda: db.reference(f"sessions/{cli_session_id}").set(payload))

	async def delete_session_record(self, cli_session_id: str) -> None:
		def _delete():
			db.reference(f"sessions/{cli_session_id}").delete()
			db.reference(f"session_acks/{cli_session_id}").delete()
		await asyncio.to_thread(_delete)

	async def set_session_home(self, session_id: str, conv_id: str | None) -> None:
		"""Persist a cli session's home-conversation pointer.

		Pass conv_id=None to delete the stored pointer (idempotent — Firebase
		delete on a missing node is a no-op). Used by session-fallback to clear
		a stale home pointer once the home conversation has ended and the
		session has gone dormant."""
		ref = db.reference(f"cli_sessions/{session_id}/home_conversation_id")
		if conv_id is None:
			await asyncio.to_thread(ref.delete)
		else:
			await asyncio.to_thread(ref.set, conv_id)

	async def write_conversation_member(self, conv_id: str, member) -> None:
		"""Write a member entry under /conversations/<id>/members_active/<sender>."""
		ref = db.reference(f"conversations/{conv_id}/members_active/{member.sender}")
		await asyncio.to_thread(ref.set, _member_payload(member))

	async def set_conversation_state(self, conv_id: str, state: str) -> None:
		"""Update a conversation's state (active/ended) at /conversations/<id>/meta/state.
		Hydration reads from meta.state — writing the top-level /state path was a bug
		that let Ended conversations resurrect on restart."""
		ref = db.reference(f"conversations/{conv_id}/meta/state")
		await asyncio.to_thread(ref.set, state)

	async def write_conversation_meta(
		self,
		conv_id: str,
		*,
		title: str,
		state: str,
		continued_from: str | None,
		created_at: float,
		last_activity_at: float,
		ended_at: float | None,
		hidden: bool,
		origin: str | None = None,
	) -> None:
		"""Write the top-level conversation fields (everything except members and messages)."""
		ref = db.reference(f"conversations/{conv_id}/meta")
		# update() not set(): set() overwrites the whole /meta node and would
		# clobber sibling fields (preview) if ever called on an existing
		# conversation (F-80). All current callers are creation-only, but the
		# partial write makes the method safe to call post-creation too.
		payload = {
			"title": title[:80],
			"state": state,
			"continued_from": continued_from,
			"created_at": created_at,
			"last_activity_at": last_activity_at,
			"ended_at": ended_at,
			"hidden": hidden,
		}
		if origin is not None:
			payload["origin"] = origin
		await asyncio.to_thread(lambda: ref.update(payload))

	async def remove_conversation_member(self, conv_id: str, sender: str) -> None:
		"""Remove a member entry under /conversations/<id>/members_active/<sender>."""
		ref = db.reference(f"conversations/{conv_id}/members_active/{sender}")
		await asyncio.to_thread(ref.delete)

	async def move_conversation_member(self, source_id: str, target_id: str, member, old_sender: str, *, end_source: bool = False) -> None:
		"""Move a member between conversations as ONE multi-location update
		(REV-104): remove from source, add to target, optionally end the
		source. Member nodes are keyed by the raw sender string (the same
		convention as write/remove_conversation_member); the state path is
		meta/state - hydration reads meta.state, the top-level path was a
		resurrection bug."""
		updates = {
			f"conversations/{source_id}/members_active/{old_sender}": None,
			f"conversations/{target_id}/members_active/{member.sender}": _member_payload(member),
		}
		if end_source:
			updates[f"conversations/{source_id}/meta/state"] = "ended"
		ref = db.reference()
		await asyncio.to_thread(ref.update, updates)

	async def write_conversation_member_history(self, conv_id: str, member) -> None:
		"""Write a departed member to /conversations/<id>/members_history/<sender>.
		Keyed by sender (same convention as members_active). Includes parting
		metadata (left_at, session_ended_at, session_end_reason) so hydration can
		restore members_history after restart."""
		ref = db.reference(f"conversations/{conv_id}/members_history/{member.sender}")
		await asyncio.to_thread(ref.set, {
			"cli_session_id": member.cli_session_id,
			"sender": member.sender,
			"cwd": member.cwd,
			"surface": member.surface,
			"alive": member.alive,
			"session_lost_permanently": member.session_lost_permanently,
			"session_ended_at": member.session_ended_at,
			"session_end_reason": member.session_end_reason,
			"joined_at": member.joined_at,
			"left_at": member.left_at,
			"last_seen_seq": member.last_seen_seq,
		})

	async def write_conversation_message(
		self,
		conv_id: str,
		sender_or_message,
		message_type: str | None = None,
		text: str | None = None,
		*,
		request_id: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
		title: str | None = None,
		url: str | None = None,
		filename: str | None = None,
		attached_to_msg_id: str | None = None,
		rejected: bool = False,
		suppress_push: bool = False,
	):
		"""Append a message to /messages/<conv_id>.

		Two call forms are accepted:

		1. Legacy dict form (used by conversation_ops / dispatch callers that pass
		   the in-memory message dict directly):
		     write_conversation_message(conv_id, message_dict) -> str (push key)

		2. Expanded keyword form (matches write_channel_message semantics; used by
		   all migrated callers that previously called write_channel_message):
		     write_conversation_message(conv_id, sender, message_type, text, ...)
		     -> tuple[CorrelationToken, str | None]  i.e. (correlation, msg_id)

		Returns:
		  - dict form: str push key (for backward compat)
		  - expanded form: (correlation, msg_id) tuple matching write_channel_message
		    return semantics so callers are drop-in replaceable.
		"""
		from datetime import datetime, timezone

		# ---- Legacy dict form: second arg is a dict --------------------------------
		if isinstance(sender_or_message, dict):
			message = sender_or_message
			ref = db.reference(f"messages/{conv_id}").push()
			await asyncio.to_thread(ref.set, message)
			return ref.key

		# ---- Expanded form: second arg is sender string ----------------------------
		sender = sender_or_message
		if message_type is None or text is None:
			raise TypeError("write_conversation_message: message_type and text are required when sender is a str")

		# Document upload: if url is a local path, upload to Firebase Storage first
		effective_url = url
		effective_filename = filename
		storage_path = None
		if message_type == "document" and url and not (url.startswith("http://") or url.startswith("https://")):
			try:
				from pathlib import Path as _Path
				path = _Path(url)
				effective_url, storage_path = await self._upload_file(path)
				if effective_filename is None:
					effective_filename = path.name
				if self._logger:
					await self._logger.info(f"firebase_upload_success: {effective_url}")
			except Exception as exc:
				# Fail loudly: do NOT fall through writing the local filesystem
				# path as the message url (the phone can't fetch it, and the
				# agent would be told "ok"). Re-raise so send_document_human's
				# try/except returns an ERROR string to the agent.
				if self._logger:
					await self._logger.surface_error(f"firebase_upload_failed: {exc}")
				raise

		now = datetime.now(timezone.utc).isoformat()
		payload = {
			"type": message_type,
			"sender": sender,
			"text": text,
			"format": format,
			"timestamp": now,
			"cancelled": False,
			"rejected": rejected,
		}
		if title is not None:
			payload["title"] = title[:80]
		if request_id is not None:
			payload["request_id"] = request_id
		if effective_url is not None:
			payload["url"] = effective_url
		if effective_filename is not None:
			payload["filename"] = effective_filename
		if storage_path is not None:
			payload["storage_path"] = storage_path
		if suggestions is not None:
			payload["suggestions"] = list(suggestions)
		if attached_to_msg_id is not None:
			payload["attached_to_msg_id"] = attached_to_msg_id

		if self._logger:
			await self._logger.info(f"firebase_write_conv_message: {conv_id}")

		# Auto-unhide conversation on question writes — mirror the write_channel_message
		# behaviour so the phone surfaces the conversation when a question arrives.
		if message_type == "question":
			await asyncio.to_thread(lambda: db.reference(f"conversations/{conv_id}/meta/hidden").set(False))

		ref = db.reference(f"messages/{conv_id}").push()
		await asyncio.to_thread(ref.set, payload)
		msg_id = ref.key

		# Atomically increment unread_count for every non-human message so the
		# Android badge reflects unread activity on all device surfaces.
		if message_type != "human":
			try:
				def _bump_unread():
					db.reference(f"conversations/{conv_id}/unread_count").set(_increment(1))
				await asyncio.to_thread(_bump_unread)
			except Exception:
				pass  # best-effort; don't fail the message write on counter glitch

		# Update conversation-level last_activity_at + preview (mirrors channel behaviour).
		# last_activity_at is a FLOAT seconds-since-epoch in meta (Android reads it as
		# Double); the message's `timestamp` field above uses ISO-string for the message
		# log. Using the iso string here would clobber the float and break Android's
		# parse, dropping the conversation row from Page A.
		preview = text[:120].replace("\n", " ").strip()
		now_ts = datetime.now(timezone.utc).timestamp()
		await asyncio.to_thread(lambda: db.reference(f"conversations/{conv_id}/meta").update({
			"last_activity_at": now_ts,
			"preview": preview,
		}))

		# FCM notification (same topics / payload as write_channel_message).
		# suppress_push (REV-109): a rate-limited agent_msg still writes and
		# bumps unread, but must not buzz the phone.
		if message_type != "human" and not suppress_push:
			fcm_data: dict = {
				"conv_id": conv_id,
				"sb_message_type": message_type,
				"message_id": msg_id,
			}
			if message_type == "question" and request_id is not None:
				fcm_data["request_id"] = request_id
			try:
				await self._send_fcm(conv_id, message_type, sender, text, fcm_data)
			except Exception as exc:
				if self._logger:
					await self._logger.surface_error(f"firebase_fcm_error: {exc}")

		# Correlation is the conversation id; answers resolve by (conv_id, request_id).
		return conv_id, msg_id

	async def set_conversation_last_activity(self, conv_id: str, ts: float) -> None:
		"""Update /conversations/<id>/meta/last_activity_at."""
		ref = db.reference(f"conversations/{conv_id}/meta/last_activity_at")
		await asyncio.to_thread(ref.set, ts)

	async def write_conversation_title(self, conv_id: str, title: str) -> None:
		"""Update /conversations/<id>/meta/title without touching sibling fields.
		Targeted update (not a full meta set) so a post-creation title change
		from message_and_await_agent reaches the phone without clobbering
		preview, state, or activity timestamps (M3)."""
		ref = db.reference(f"conversations/{conv_id}/meta")
		await asyncio.to_thread(lambda: ref.update({"title": title[:80]}))

	def _schedule_command_delete(self, node: str, cmd_id: str) -> None:
		"""Bridge-safe Firebase delete from a listener-thread callback (M32):
		loop.run_in_executor is not thread-safe off-loop, so bounce via
		call_soon_threadsafe and do the blocking delete in a worker thread."""
		def _delete():
			db.reference(f"{node}/{cmd_id}").delete()
		self._loop.call_soon_threadsafe(
			lambda: _spawn_bg(asyncio.to_thread(_delete), label=f"fb_command_delete:{node}/{cmd_id}"),
		)

	def _start_command_listener(
		self, node: str, handler, *, name: str | None = None,
		delete_before_dispatch: bool = False, stale_notice: bool = True,
	) -> None:
		"""Shared listener for the /<node> command queues (combine_commands,
		force_end_commands, spawn_commands, away_mode_commands, widget/status_request).

		- `name` overrides the /healthz supervisor key (defaults to node).
		- `stale_notice` suppresses the phone notice for transient idempotent
		  commands (the drop is still logged and deleted).
		- Processes the initial snapshot as well as incremental puts, so
		  commands written while the server was down are dispatched on
		  (re)connect (H12/M13; T-015 queue-until-online).
		- Gates every dispatch on the command's issued_at freshness: commands
		  older than COMMAND_TTL_SECONDS are deleted with a phone-visible
		  notice (unless stale_notice=False), never executed and never silently
		  dropped, and always logged (decided 2026-06-11). Missing/unparseable
		  stamps fail open (dispatch).
		- Delivery is at-least-once for idempotent handlers (combine/
		  force_end/convene): the delete runs only after the handler completes
		  without raising, so a crash or a raising handler leaves the entry to
		  re-deliver on the next restart snapshot; a per-run processed-id set
		  dedupes redeliveries within this process. A deduped redelivery is
		  not re-deleted; a lingering entry is executed or TTL-dropped by a
		  later restart.
		- delete_before_dispatch=True switches to at-most-once for a
		  NON-idempotent handler (spawn mints a conversation and launches a
		  process every run, so a replay double-spawns). The command is
		  deleted and the delete is awaited BEFORE the handler runs, so a
		  crash cannot replay it from the next restart snapshot. A lost spawn
		  (crash after the delete commits but before the launcher fires) is
		  the safer failure mode — John re-taps when nothing appears, versus a
		  confusing duplicate agent in a phantom conversation.
		- Runs on the Firebase SDK listener thread: every loop-affined call
		  is bridged via call_soon_threadsafe (M32).
		"""
		from server.firebase_supervisor import SupervisedListener
		key = name or node
		if key in self._supervised:
			return

		processed: set[str] = set()

		def _dispatch_entry(cmd_id: str, cmd: dict) -> None:
			if cmd_id in processed:
				return
			processed.add(cmd_id)
			age = command_age_seconds(cmd.get("issued_at"))
			if age is not None and age > COMMAND_TTL_SECONDS:
				if self._logger:
					log_coro = self._logger.surface_error(
						f"stale_command_dropped: {key}/{cmd_id} issued_at={cmd.get('issued_at')}"
					)
					self._loop.call_soon_threadsafe(
						lambda c=log_coro: _spawn_bg(c, label=f"fb_stale_command_log:{key}/{cmd_id}"),
					)
				if stale_notice:
					notice = (
						f"Dropped stale {node[:-1].replace('_', ' ')} from {cmd.get('issued_at')}: "
						f"the server was offline when it was sent and it is older than "
						f"{COMMAND_TTL_SECONDS // 60} minutes. Re-send it if still wanted."
					)
					coro = self.send_text(notice)
					self._loop.call_soon_threadsafe(
						lambda c=coro: _spawn_bg(c, label=f"fb_stale_command_notice:{node}/{cmd_id}"),
					)
				self._schedule_command_delete(node, cmd_id)
				return
			if delete_before_dispatch:
				# At-most-once for the non-idempotent spawn handler: delete the
				# command and await that delete, THEN run the handler, so a
				# crash cannot replay the command and double-launch.
				async def _delete_then_handle(c=cmd, cid=cmd_id):
					await asyncio.to_thread(lambda: db.reference(f"{node}/{cid}").delete())
					await handler(c)
				self._loop.call_soon_threadsafe(
					lambda: _spawn_bg(_delete_then_handle(), label=f"fb_command:{node}/{cmd_id}"),
				)
				return
			# Route via _spawn_bg so a hanging or raising handler shows up in
			# logs and the task isn't GC-eligible mid-execution. The delete runs
			# only AFTER the handler returns cleanly (REV-105): a raising
			# handler leaves the command entry in Firebase, so the next
			# restart's snapshot re-delivers it (real at-least-once). Within
			# this process the `processed` set suppresses a replay; a
			# re-delivered entry older than the TTL takes the stale branch
			# above instead.
			async def _handle_then_delete(c=cmd, cid=cmd_id):
				await handler(c)
				await asyncio.to_thread(lambda: db.reference(f"{node}/{cid}").delete())
			self._loop.call_soon_threadsafe(
				lambda: _spawn_bg(_handle_then_delete(), label=f"fb_command:{node}/{cmd_id}"),
			)

		def _on_event(event):
			if event.event_type != "put" or not event.data:
				return
			path = event.path.strip("/")
			data = event.data
			if not path:
				# Initial/reconnect snapshot: the whole node.
				if isinstance(data, dict):
					for cmd_id, cmd in data.items():
						if isinstance(cmd, dict):
							_dispatch_entry(cmd_id, cmd)
				return
			if "/" in path:
				return  # nested field write; commands are pushed whole
			if isinstance(data, dict):
				_dispatch_entry(path, data)

		err = self._logger.surface_error if self._logger else _no_op_async_logger
		sup = SupervisedListener(
			name=key,
			path=node,
			callback=_on_event,
			error_logger=err,
			loop=self._loop,
		)
		self._supervised[key] = sup
		sup.start()

	async def start_combine_command_listener(self, handler) -> None:
		"""Listen for entries under /combine_commands; dispatch handler(cmd_dict)
		for each queued or new entry (initial snapshot included; TTL-gated)."""
		self._start_command_listener("combine_commands", handler)

	async def start_force_end_command_listener(self, handler) -> None:
		"""Listen for entries under /force_end_commands; dispatch handler(cmd_dict)
		for each queued or new entry (initial snapshot included; TTL-gated)."""
		self._start_command_listener("force_end_commands", handler)

	async def start_convene_command_listener(self, handler) -> None:
		"""Listen for entries under /convene_commands; dispatch handler(cmd_dict)
		for each queued or new entry (initial snapshot included; TTL-gated).
		At-least-once: convene routing is idempotent (already-a-member counts as
		success), so replay after a crash re-converges instead of duplicating."""
		self._start_command_listener("convene_commands", handler)

	async def start_conversation_answers_listener(self) -> None:
		"""Listen for answer events under /answers/<conv_id>/<request_id>.

		correlation is the conv_id; request_id (from the answer path) is the
		resolution key. sender rides along for display/logging only.

		Answers payload written by the phone/Operator: {text, sender,
		request_id, written_at}. The slot stored in IncomingResponse is the
		full RTDB path 'answers/<conv_id>/<request_id>' so
		delete_response_slot can delete it directly.
		"""
		from server.firebase_supervisor import SupervisedListener

		listener_name = "conversation_answers"
		if listener_name in self._supervised:
			return

		def _enqueue_answer(conv_id: str, request_id: str, data: dict) -> None:
			text = data.get("text")
			sender = data.get("sender")
			if not isinstance(text, str) or not isinstance(sender, str):
				# Malformed writes must not vanish silently. This runs on the
				# Firebase listener thread, so bounce the log write to the loop
				# with per-call bindings (the lambda default pins THIS event's
				# coroutine, not the loop variable).
				if self._logger:
					log_coro = self._logger.surface_error(
						f"malformed_answer_dropped: answers/{conv_id}/{request_id} "
						f"text_type={type(text).__name__} sender_type={type(sender).__name__}"
					)
					self._loop.call_soon_threadsafe(
						lambda c=log_coro: _spawn_bg(c, label=f"malformed_answer_log:{conv_id}:{request_id}"),
					)
				return
			slot = f"answers/{conv_id}/{request_id}"  # full path for the slot delete
			resp = IncomingResponse(correlation=conv_id, text=text, slot=slot, request_id=request_id, sender=sender)
			# Bounce to the event loop: this runs on the Firebase listener
			# thread, and _spawn_bg / asyncio.create_task require a running loop.
			# Without this bounce, every answer event raises RuntimeError inside
			# the SupervisedListener wrapper and the reply is silently lost.
			coro = self._response_queue.put(resp)
			self._loop.call_soon_threadsafe(
				lambda c=coro: _spawn_bg(c, label=f"conv_answer_enqueue:{conv_id}:{sender}"),
			)

		def _on_answer(event):
			if event.event_type != "put" or not event.data:
				return
			path = event.path.strip("/")
			if not path:
				# Initial/reconnect snapshot: data is the whole /answers node,
				# {conv_id: {request_id: entry}}. Replay any undelivered
				# answers (H06): a reply written while the listener was
				# detached would otherwise never be enqueued and the asker
				# would block the full 24h. Idempotent: dispatch_responses
				# deletes the slot on resolve and stale-notices unknown
				# correlations.
				data = event.data
				if not isinstance(data, dict):
					return
				for conv_id, answers in data.items():
					if not isinstance(answers, dict):
						continue
					for request_id, entry in answers.items():
						if isinstance(entry, dict):
							_enqueue_answer(conv_id, request_id, entry)
				return
			# Incremental put, path form: <conv_id>/<request_id>
			parts = path.split("/")
			if len(parts) != 2:
				return
			data = event.data
			if not isinstance(data, dict):
				return
			_enqueue_answer(parts[0], parts[1], data)

		err = self._logger.surface_error if self._logger else _no_op_async_logger
		sup = SupervisedListener(
			name=listener_name,
			path="answers",
			callback=_on_answer,
			error_logger=err,
			loop=self._loop,
		)
		self._supervised[listener_name] = sup
		sup.start()

	async def start_spawn_command_listener(self, handler) -> None:
		"""Listen for entries under /spawn_commands; dispatch handler(cmd_dict)
		for each queued or new entry (initial snapshot included; TTL-gated).

		delete_before_dispatch=True: spawn is non-idempotent (mints a
		conversation + launches a process), so the command is deleted before
		the handler runs to make replay-after-crash impossible (at-most-once)."""
		self._start_command_listener("spawn_commands", handler, delete_before_dispatch=True)
