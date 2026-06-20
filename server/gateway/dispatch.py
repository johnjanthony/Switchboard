from __future__ import annotations

import asyncio
import json as _json
from pathlib import Path as _Path
from server.registry import Registry
from server.logging_jsonl import JsonlLogger
from server.messenger import (
	MessageWriter,
	ResponsePoller,
	ConversationStore,
)
from server.gateway.bg_tasks import _spawn_bg
from server.firebase_supervisor import LoopSupervisor
from server.command_freshness import COMMAND_TTL_SECONDS, command_age_seconds

class _DispatchResponsesBackend(ResponsePoller, MessageWriter, ConversationStore):
	"""Backend surface used by dispatch_responses."""


async def _sweep_session_end_markers(registry, marker_dir, backend=None, logger=None) -> int:
	"""Process and delete SessionEnd marker files written by the hook.

	Each marker is `<marker_dir>/<session_id>.json` with {session_id, reason,
	ended_at}. For each, call handle_session_end (using the marker's recorded
	ended_at as `now`, so session_ended_at reflects when the session actually
	ended, not sweep time), then delete the marker. Reprocessing is harmless:
	handle_session_end unbinds first, so a re-run logs session_end_no_binding and
	no-ops. Returns the number of markers processed."""
	from server.cli_session_end import handle_session_end
	from datetime import datetime, timezone
	d = _Path(marker_dir)
	if not d.exists():
		return 0
	count = 0
	for marker_path in sorted(d.glob("*.json")):
		try:
			data = _json.loads(marker_path.read_text(encoding="utf-8"))
			session_id = data.get("session_id")
			reason = data.get("reason", "other")
			# Use the marker's recorded end time so session_ended_at reflects when
			# the session actually ended, not sweep time. Markers from the hook
			# always include ended_at; fall back defensively if one does not.
			ended_at = data.get("ended_at") or datetime.now(timezone.utc).isoformat()
			if session_id:
				await handle_session_end(
					registry=registry,
					session_id=session_id,
					reason=reason if isinstance(reason, str) else "other",
					now=lambda ts=ended_at: ts,
					backend=backend,
					logger=logger,
				)
				count += 1
		except Exception as exc:
			if logger is not None:
				await logger.surface_error(f"session_end_marker_failed: {marker_path.name}: {exc}")
		finally:
			try:
				marker_path.unlink()
			except OSError:
				pass
	return count


async def dispatch_session_end_markers(registry, backend, logger, supervisor, marker_dir, interval: float = 5.0) -> None:
	"""Periodically sweep SessionEnd marker files and mark members dormant.

	Marker-file delivery (not the racy SessionEnd HTTP POST) is the reliable
	path: Claude Code SessionEnd hooks are fire-and-forget and do not block
	process exit, so a synchronous POST gets dropped. The hook writes a marker
	file (a fast filesystem write that wins the exit race); this loop applies it
	via handle_session_end. The first tick after a restart drains markers left
	from sessions that ended while the server was down."""
	while True:
		try:
			await _sweep_session_end_markers(registry, marker_dir, backend=backend, logger=logger)
			supervisor.record_success()
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			await supervisor.record_crash(exc)
		await asyncio.sleep(interval)


async def dispatch_responses(
	registry: Registry,
	backend: _DispatchResponsesBackend,
	logger: JsonlLogger,
	supervisor: LoopSupervisor,
) -> None:
	from server.gateway.handlers import _append_session_log
	while True:
		try:
			async for response in backend.poll_responses():
				supervisor.record_success()
				try:
					corr = response.correlation
					if isinstance(corr, tuple) and len(corr) == 2:
						conversation_id, sender = corr
						record = registry.get((conversation_id, sender))
						req_id = registry.resolve(conversation_id, sender, response.text, request_id=response.request_id)
						if req_id is None:
							await logger.surface_error(f"unknown_correlation: conversation_id={conversation_id} sender={sender}")
							try:
								await backend.send_stale_reply_notice(conversation_id, sender)
							except Exception as exc:
								await logger.surface_error(f"stale_reply_notice_failed: {exc}")
							# Drop the orphan from `responses/` so the listener doesn't
							# re-fire it on every server restart. We've already logged
							# and surfaced the stale notice; the response can't be
							# routed and there's no point keeping it around.
							if response.slot:
								try:
									await backend.delete_response_slot(response.slot)
								except Exception as exc:
									await logger.surface_error(
										f"delete_stale_response_failed: slot={response.slot} {exc}"
									)
						elif record is not None:
							# Drop the response slot now that resolution is committed.
							# Done here (rather than only in handlers.ask_human's
							# success path) so the cleanup runs even when the agent
							# coroutine dies between future-resolve and post-resolve
							# bookkeeping — common with MCP streamable-HTTP transport,
							# whose disconnect propagation is not reliable.
							if response.slot:
								_spawn_bg(
									backend.delete_response_slot(response.slot),
									label=f"response_slot_cleanup:{response.slot}",
								)
							# Add a NEW message to the history so it shows up in-line in the app.
							# The attached_to_msg_id field links it back to the question; the client
							# uses this to splice the reply directly under its question and to derive
							# the answered-state for the question's RESPONDED badge.
							attached = record.msg_id
							async def _write_history(cid=conversation_id, txt=response.text, attached=attached):
								try:
									await backend.write_conversation_message(
										cid, "John", "human", txt,
										attached_to_msg_id=attached,
									)
									await logger.notify_sent(cid, f"Reply: {txt}")
									await _append_session_log(logger.log_path, cid, "←", txt, logger)
								except Exception as exc:
									await logger.surface_error(f"history_write_failed: {exc}")
							_spawn_bg(_write_history(), label=f"history_write:{conversation_id}")
					else:
						await logger.surface_error(f"legacy_correlation_dropped: {corr}")
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(
						f"dispatch_iteration_error: {exc}",
						correlation=str(response.correlation),
					)
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			await supervisor.record_crash(exc)

async def handle_force_end(registry, conversation_id: str, backend=None, logger=None) -> None:
	"""Force-end a conversation: resolve all waiters with sentinel, clear members,
	apply session-fallback for each session.

	Idempotent: if conversation is already Ended or doesn't exist, returns cleanly.

	backend: optional ConversationStore — if provided, Firebase writes are issued
	for member removal, state change, open-pointer clear, and a force-end system message.
	"""
	import time as _time
	from datetime import datetime, timezone
	from server.session_fallback import apply_fallback

	conv = registry.conversations.get(conversation_id)
	if not conv or conv.state == "ended":
		return  # idempotent

	async with conv.lock:
		# Resolve every queued future with the __CONVERSATION_ENDED__ sentinel
		while conv.wait_queue:
			entry = conv.wait_queue.popleft()
			future = entry["future"]
			if not future.done():
				future.set_result("__CONVERSATION_ENDED__\n(force-ended)")
		# Also resolve any mint-path opener blocked on open_peer_future
		opener_future = conv.open_peer_future
		if opener_future is not None and not opener_future.done():
			opener_future.set_result("__CONVERSATION_ENDED__\n(force-ended)")
		conv.open_peer_future = None

		# Collect session_ids for fallback (before clearing members).
		# Both alive and dormant member sessions go through apply_fallback so
		# their home-pointer state stays consistent with the conv's lifecycle.
		# apply_fallback detects dormant sessions internally (binding already
		# cleared by cli_session_end) and performs cleanup-only: it clears the
		# home pointer if it referenced this (now-Ended) conversation, without
		# creating a new conversation or trying to unbind again.
		session_ids = [m.cli_session_id for m in conv.members_active.values()]

		# Collect member senders for Firebase removal
		member_senders = list(conv.members_active.keys())

		# Clear active membership
		conv.members_active.clear()

		# Mark Ended
		conv.state = "ended"
		conv.ended_at = _time.time()
		open_cleared = False
		if registry.open_conversation_id == conversation_id:
			registry.open_conversation_id = None
			open_cleared = True

		if backend is not None:
			now_iso = datetime.now(timezone.utc).isoformat()
			force_end_msg = {
				"seq": len(conv.messages),
				"sender": "<system>",
				"type": "system",
				"text": "Conversation force-ended.",
				"timestamp": now_iso,
			}
			conv.messages.append(force_end_msg)
			for sender in member_senders:
				_spawn_bg(
					backend.remove_conversation_member(conversation_id, sender),
					label=f"fb_remove_member:{conversation_id}:{sender}",
				)
			_spawn_bg(
				backend.set_conversation_state(conversation_id, "ended"),
				label=f"fb_set_state:{conversation_id}:ended",
			)
			if open_cleared:
				_spawn_bg(
					backend.set_open_conversation_id(None),
					label=f"fb_clear_open_id:{conversation_id}",
				)
			_spawn_bg(
				backend.write_conversation_message(conversation_id, force_end_msg),
				label=f"fb_write_force_end_msg:{conversation_id}",
			)

	# Resolve any pending ask_human futures for this conversation (H02) with the
	# terminal __CONVERSATION_ENDED__ sentinel and mark their Firebase question
	# records cancelled so the phone's pending list clears. Resolving (rather
	# than cancelling) the future hands the agent a semantic value it returns
	# normally, so it stops instead of reading a cancellation as a transport
	# error and retrying onto orphan/home state (T-145). mark_question_cancelled
	# also removes the pending_questions record; the registry's pending-mirror
	# fires the badge decrement.
	cancelled = registry.resolve_pending_for_conversation(
		conversation_id, "__CONVERSATION_ENDED__\n(force-ended)"
	)
	if backend is not None:
		for request_id in cancelled:
			try:
				await backend.mark_question_cancelled(conversation_id, request_id)
			except Exception as exc:
				if logger is not None:
					await logger.surface_error(
						f"force_end_mark_cancelled_failed: conv={conversation_id} req={request_id} {exc}"
					)

	# Apply session-fallback for each member's session (alive AND dormant).
	# apply_fallback dispatches internally based on binding state.
	for sid in session_ids:
		apply_fallback(registry, sid, backend=backend)


async def dispatch_combine_commands(registry, backend, logger, supervisor, pending_dir=None):
	"""Watch /combine_commands for new entries; route to _perform_combine.

	combine command shape:
	{
		"source_conversation_id": "<conv-id>",
		"target_conversation_id": "<conv-id>",
		"issued_at": "<ISO-8601>",
	}

	The Firebase listener stubs on the backend (start_combine_command_listener) are
	pass-through no-ops until Task 31's full listener implementation is wired. The
	dispatcher is fully functional once the backend method is implemented.
	pending_dir: pathlib.Path for spawn-pending files, or None (skips dormant relaunch).
	"""
	from server.conversation_ops import _perform_combine as _conv_perform_combine

	async def _handle(cmd: dict, ack=None):
		source_id = cmd.get("source_conversation_id")
		target_id = cmd.get("target_conversation_id")
		if not source_id or not target_id:
			await logger.surface_error(f"combine_command_missing_ids: {cmd}")
			return
		result = await _conv_perform_combine(registry, source_id, target_id, logger, pending_dir=pending_dir, backend=backend)
		await logger.info(f"combine_command_handled: {result}")

	if hasattr(backend, "start_combine_command_listener"):
		await backend.start_combine_command_listener(_handle)
	else:
		await logger.info("combine_command_listener not wired (backend missing method)")


async def dispatch_force_end_commands(registry, backend, logger, supervisor):
	"""Watch Firebase /force_end_commands/ for force-end requests.
	On each command, call handle_force_end(registry, conversation_id).

	The Firebase listener stub on the backend (start_force_end_command_listener) is a
	pass-through no-op until the full listener implementation is wired.
	"""
	async def _handle(cmd: dict, ack=None):
		conv_id = cmd.get("conversation_id")
		if not conv_id:
			await logger.surface_error(f"force_end_command_missing_id: {cmd}")
			return
		await handle_force_end(registry, conv_id, backend=backend, logger=logger)
		await logger.info(f"force_end_command_handled: conv_id={conv_id}")

	if hasattr(backend, "start_force_end_command_listener"):
		await backend.start_force_end_command_listener(_handle)
	else:
		await logger.info("force_end_command_listener not wired (backend missing method)")


async def dispatch_spawn_commands(spawn_handler, backend, logger, supervisor):
	"""Watch /spawn_commands for new entries; route on cmd['type'].

	Dispatches 'fresh' and 'resume' spawn commands to SpawnHandler.
	The Firebase listener stub on the backend (start_spawn_command_listener) is a
	pass-through no-op until the full listener implementation is wired.
	"""

	async def _handle(cmd: dict, ack=None):
		cmd_type = cmd.get("type")
		if cmd_type == "fresh":
			await spawn_handler.handle_fresh(cmd)
		elif cmd_type == "resume":
			await spawn_handler.handle_resume(cmd)
		else:
			await logger.surface_error(f"spawn_command_unknown_type: {cmd_type}")
			return
		await logger.info(f"spawn_command_handled: type={cmd_type}")

	if hasattr(backend, "start_spawn_command_listener"):
		await backend.start_spawn_command_listener(_handle)
	else:
		await logger.info("spawn_command_listener not wired (backend missing method)")


async def dispatch_away_mode_commands(registry, backend, logger, supervisor):
	"""Watch /away_mode_commands for phone-initiated away mode toggles.

	Command shapes (Android-emitted via MainViewModel):
	- {type: "enter_global", issued_at: "<ISO>"}
	- {type: "exit_global", issued_at: "<ISO>", decision?: "send_default"|"skip"|"cancel", default_text?: "<text>"}

	enter_global flips global_away_mode True.
	exit_global applies the decision via _apply_bulk_respond_decision and flips False only when the decision commits (send_default with non-blank text, skip, or no-decision with no pendings).
	"""
	from server.gateway.bulk_respond import _apply_bulk_respond_decision

	while True:
		try:
			async for cmd in backend.poll_away_mode_commands():
				supervisor.record_success()
				try:
					cmd_type = cmd.get("type")
					# Belt-and-braces over P1-5's startup clear (decided
					# 2026-06-11): a stale toggle that survived a
					# crash-before-delete replay must not flip away mode
					# minutes or hours later. Dropped loudly, never silently.
					age = command_age_seconds(cmd.get("issued_at"))
					if age is not None and age > COMMAND_TTL_SECONDS:
						await logger.surface_error(
							f"away_mode_command_stale_dropped: type={cmd_type} issued_at={cmd.get('issued_at')}"
						)
						try:
							await backend.send_text(
								f"Dropped stale away-mode command ({cmd_type}) from {cmd.get('issued_at')}: "
								f"older than {COMMAND_TTL_SECONDS // 60} minutes. Re-send it if still wanted."
							)
						except Exception as exc:
							await logger.surface_error(f"stale_away_notice_failed: {exc}")
						continue
					if cmd_type == "enter_global":
						registry.global_away_mode = True
						try:
							await backend.set_global_away_mode(True)
						except Exception as exc:
							await logger.surface_error(f"away_mode_enter_persist_failed: {exc}")
						await logger.info("away_mode_enter_global")

					elif cmd_type == "exit_global":
						decision = cmd.get("decision")
						default_text = cmd.get("default_text") or ""
						# Legacy payloads sent only default_text; map them onto
						# the decision contract so the field is authoritative
						# without breaking older app builds (M07).
						if decision is None and default_text:
							decision = "send_default"
						commit = False
						try:
							commit = await _apply_bulk_respond_decision(
								registry, backend, logger,
								decision=decision,
								default_text=default_text,
							)
						except Exception as exc:
							await logger.surface_error(f"bulk_respond_failed: {exc}")
						if commit:
							registry.global_away_mode = False
							try:
								await backend.set_global_away_mode(False)
							except Exception as exc:
								await logger.surface_error(f"away_mode_exit_persist_failed: {exc}")
						await logger.info(
							f"away_mode_exit_global decision={decision!r} committed={commit}"
						)

					else:
						await logger.surface_error(f"away_mode_command_unknown_type: {cmd_type}")

				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"away_mode_command_iteration_error: {exc}")

		except asyncio.CancelledError:
			raise
		except Exception as exc:
			await supervisor.record_crash(exc)


# dispatch_inject_queue was retired when Android dropped inject_queue writes.
# The legacy CollabSession BYO flow that consumed these no longer exists.
# registry.get_session() was deleted with that cleanup. The inject-listener
# trait, its firebase implementation, and the associated backend-contract
# surface were all removed in Fix Pack 3.
