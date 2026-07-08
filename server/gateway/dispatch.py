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
from server.gateway.parked import finish_parked_resolve
from server.firebase_supervisor import LoopSupervisor
from server.command_freshness import COMMAND_TTL_SECONDS, command_age_seconds

class _DispatchResponsesBackend(ResponsePoller, MessageWriter, ConversationStore):
	"""Backend surface used by dispatch_responses."""


async def _sweep_session_end_markers(registry, marker_dir, backend=None, logger=None, session_registry=None) -> int:
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
					session_registry=session_registry,
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


async def dispatch_session_end_markers(
	registry, backend, logger, supervisor, marker_dir, interval: float = 5.0, session_registry=None,
) -> None:
	"""Periodically sweep SessionEnd marker files and mark members dormant.

	Marker-file delivery (not the racy SessionEnd HTTP POST) is the reliable
	path: Claude Code SessionEnd hooks are fire-and-forget and do not block
	process exit, so a synchronous POST gets dropped. The hook writes a marker
	file (a fast filesystem write that wins the exit race); this loop applies it
	via handle_session_end. The first tick after a restart drains markers left
	from sessions that ended while the server was down."""
	while True:
		try:
			await _sweep_session_end_markers(
				registry, marker_dir, backend=backend, logger=logger, session_registry=session_registry,
			)
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
	session_registry=None,
) -> None:
	from server.gateway.handlers import _append_session_log
	while True:
		try:
			async for response in backend.poll_responses():
				supervisor.record_success()
				try:
					conversation_id = response.correlation if isinstance(response.correlation, str) else None
					if conversation_id and response.request_id:
						record = registry.find_by_request_id(conversation_id, response.request_id)
						req_id = registry.resolve(conversation_id, response.request_id, response.text)
						if req_id is None:
							# No live pending for this correlation. Distinguish a
							# benign replay of an answer we already delivered/ended
							# (the answers-listener reconnect snapshot can re-enqueue
							# an answer whose fire-and-forget slot delete had not yet
							# committed) from a genuinely unknown correlation. Only
							# the latter gets the phone-visible "reply withdrawn"
							# notice; firing it for a delivered reply is a false,
							# alarming message (M3). Either way the orphan slot is
							# dropped so it cannot re-fire on the next restart.
							if registry.was_recently_resolved(conversation_id, response.request_id):
								await logger.info(
									f"replayed_answer_ignored: conversation_id={conversation_id} request_id={response.request_id}"
								)
							else:
								await logger.surface_error(f"unknown_correlation: conversation_id={conversation_id} request_id={response.request_id}")
								try:
									await backend.send_stale_reply_notice(conversation_id, response.sender or "unknown")
								except Exception as exc:
									await logger.surface_error(f"stale_reply_notice_failed: {exc}")
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
							if record.future is None:
								await finish_parked_resolve(backend, session_registry, logger, record, response.text)
					else:
						await logger.surface_error(f"legacy_correlation_dropped: {response.correlation}")
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
	for member removal, state change, and a force-end system message.
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

		# Collect session_ids for fallback (before clearing members).
		# Both alive and dormant member sessions go through apply_fallback so
		# their home-pointer state stays consistent with the conv's lifecycle.
		# apply_fallback detects dormant sessions internally (binding already
		# cleared by cli_session_end) and performs cleanup-only: it clears the
		# home pointer if it referenced this (now-Ended) conversation, without
		# creating a new conversation or trying to unbind again.
		session_ids = list(conv.members_active.keys())

		# Collect member senders for Firebase removal
		member_senders = [m.sender for m in conv.members_active.values()]

		# Clear active membership
		conv.members_active.clear()

		# Mark Ended
		conv.state = "ended"
		conv.ended_at = _time.time()

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


async def dispatch_convene_commands(registry, session_registry, backend, logger, supervisor, spawn_handler=None):
	"""Watch /convene_commands for phone/Operator convene requests.

	Command shape: {session_ids: [...], target: "new" | "<conv-id>", title, issued_at}.
	The listener deletes entries after dispatch, so outcomes are recorded in the
	target conversation's intro message; an all-skipped convene additionally
	notifies the phone so a no-op tap is never silent."""
	from server.conversation_ops import _perform_convene

	async def _handle(cmd: dict, ack=None):
		if not isinstance(cmd.get("session_ids"), list) or not cmd.get("session_ids"):
			await logger.surface_error(f"convene_command_missing_sessions: {cmd}")
			return
		result = await _perform_convene(registry, session_registry, cmd, logger, backend=backend, spawn_handler=spawn_handler)
		if not result["convened"] and not result.get("resuming") and result["skipped"] and hasattr(backend, "send_text"):
			reasons = "; ".join(f"{s['session_id'][:8]}: {s['reason']}" for s in result["skipped"])
			try:
				await backend.send_text(f"Convene did nothing - every selected session was skipped ({reasons}).")
			except Exception as exc:
				await logger.surface_error(f"convene_noop_notice_failed: {exc}")
		await logger.info(f"convene_command_handled: {result}")

	if hasattr(backend, "start_convene_command_listener"):
		await backend.start_convene_command_listener(_handle)
	else:
		await logger.info("convene_command_listener not wired (backend missing method)")


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
		elif cmd_type == "resume_session":
			await spawn_handler.handle_resume_session(cmd)
		else:
			await logger.surface_error(f"spawn_command_unknown_type: {cmd_type}")
			return
		await logger.info(f"spawn_command_handled: type={cmd_type}")

	if hasattr(backend, "start_spawn_command_listener"):
		await backend.start_spawn_command_listener(_handle)
	else:
		await logger.info("spawn_command_listener not wired (backend missing method)")


async def dispatch_away_mode_commands(registry, backend, logger, supervisor, session_registry=None):
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
								session_registry=session_registry,
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


async def dispatch_status_request_commands(service, backend, logger, supervisor):
	"""Watch /widget-status_request for phone-initiated Claude-status checks.

	Command shape (Android-emitted): {type: "check"|"stop", issued_at: "<ISO>"}.
	check -> service.check() (fetch + maybe start the watch loop); stop -> service.stop()
	(acknowledge). The service publishes widget/status as usual, which the phone reads.
	Stale commands are dropped with a log line - a status check is transient and
	idempotent, so no phone-visible notice is warranted (unlike away-mode toggles)."""
	while True:
		try:
			async for cmd in backend.poll_status_request_commands():
				supervisor.record_success()
				try:
					cmd_type = cmd.get("type")
					age = command_age_seconds(cmd.get("issued_at"))
					if age is not None and age > COMMAND_TTL_SECONDS:
						await logger.surface_error(
							f"status_request_command_stale_dropped: type={cmd_type} issued_at={cmd.get('issued_at')}"
						)
						continue
					if cmd_type == "check":
						await service.check()
						await logger.info("status_request_check")
					elif cmd_type == "stop":
						await service.stop()
						await logger.info("status_request_stop")
					else:
						await logger.surface_error(f"status_request_command_unknown_type: {cmd_type}")
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"status_request_command_iteration_error: {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			await supervisor.record_crash(exc)


# dispatch_inject_queue was retired when Android dropped inject_queue writes.
# The legacy CollabSession BYO flow that consumed these no longer exists.
# registry.get_session() was deleted with that cleanup. The inject-listener
# trait, its firebase implementation, and the associated backend-contract
# surface were all removed in Fix Pack 3.


async def _parked_sweep_once(registry, backend, logger, *, max_age_hours, now=None):
	"""Cancel parked pendings whose ask is older than the retention horizon
	(T-001 lifetimes). The phone bubble greys out exactly as the old startup
	sweep made it - just 72h later, and only for questions nobody answered."""
	from datetime import datetime, timezone
	now_dt = now if now is not None else datetime.now(timezone.utc)
	expired = registry.expired_parked(now_dt, max_age_hours * 3600)
	for record in expired:
		registry.remove(record.conversation_id, record.cli_session_id, request_id=record.request_id)
		try:
			await backend.mark_question_cancelled(record.conversation_id, record.request_id)
		except Exception as exc:
			await logger.surface_error(f"parked_ttl_cancel_failed: {exc}")
		await logger.info(
			f"parked_pending_expired: conversation_id={record.conversation_id} request_id={record.request_id}"
		)
	return len(expired)


async def _session_sweep_once(
	session_registry, widget_store, *, lost_after_seconds, retention_hours, now_ts=None, registry=None,
):
	"""One tick of the staleness sweep, factored out so tests can drive it
	directly instead of running the infinite loop. Reads the widget store's
	last Watchtower push to decide whether ring absence is trustworthy."""
	import time as _time
	from server.session_registry import rings_are_fresh
	now = now_ts if now_ts is not None else _time.time()
	fresh = rings_are_fresh(getattr(widget_store, "pushed_at", None), now)
	ring_ids = set((getattr(widget_store, "rings", None) or {}).keys())
	live_ask_ids = live_wait_ids = None
	if registry is not None:
		live_ask_ids = {p.cli_session_id for p in registry.all_pending() if p.future is not None}
		live_wait_ids = set()
		for conv in registry.conversations.values():
			for entry in conv.wait_queue:
				member = entry.get("member")
				fut = entry.get("future")
				if member is not None and fut is not None and not fut.done():
					live_wait_ids.add(member.cli_session_id)
	return session_registry.sweep(
		now_ts=now,
		lost_after_seconds=lost_after_seconds,
		retention_seconds=retention_hours * 3600,
		rings_fresh=fresh,
		ring_ids=ring_ids,
		live_ask_ids=live_ask_ids,
		live_wait_ids=live_wait_ids,
	)


async def dispatch_session_sweep(
	session_registry, widget_store, logger, supervisor, *,
	lost_after_seconds, retention_hours, interval: float = 60.0, registry=None, backend=None,
):
	"""Periodic staleness judge for the session roster. Pure rules live in
	SessionRegistry.sweep; this loop only supplies the sensor context."""
	while True:
		try:
			pruned = await _session_sweep_once(
				session_registry, widget_store,
				lost_after_seconds=lost_after_seconds, retention_hours=retention_hours,
				registry=registry,
			)
			if pruned:
				await logger.info(f"session_sweep_pruned: {len(pruned)}")
			if registry is not None and backend is not None:
				await _parked_sweep_once(registry, backend, logger, max_age_hours=retention_hours)
			supervisor.record_success()
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			await supervisor.record_crash(exc)
		await asyncio.sleep(interval)
