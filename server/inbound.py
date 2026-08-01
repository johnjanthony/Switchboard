"""Inbound human-to-agent delivery.

One ladder, two callers: the message dispatch loop (free-form phone
messages, conversation-wide) and dispatch_responses (background-ask answers,
session-directed). Rung order per member: resolve a live blocking ask_human,
wake a wait_queue entry, else queue a session notice for hook delivery.
Callers do NOT hold conv.lock; both entry points acquire it themselves.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from server.gateway.bg_tasks import _spawn_bg
from server.gateway.pending_lifecycle import terminate_pending

INTERJECT_PREFIX = "[John interjected - not a direct answer to your question] "
NOTICE_PREFIX = "John (from phone): "


async def deliver_human_message(registry, backend, session_registry, logger, conversation_id: str, text: str) -> dict:
	conv = registry.conversations.get(conversation_id)
	if conv is None or conv.state != "active":
		return {"delivered": False, "resolved": [], "woken": [], "noticed": []}
	from server.conversation_ops import _wake_all_from
	resolved: list = []
	to_cancel: list = []
	noticed: list = []
	async with conv.lock:
		now_ts = time.time()
		conv.messages.append({
			"seq": len(conv.messages), "sender": "John", "type": "human",
			"text": text, "timestamp": datetime.now(timezone.utc).isoformat(),
		})
		conv.last_activity_at = now_ts
		_spawn_bg(
			backend.write_conversation_message(
				conversation_id, "John", "human", text, format="markdown", suppress_push=True,
			),
			label=f"fb_write_human_msg:{conversation_id}",
		)
		_spawn_bg(
			backend.set_conversation_last_activity(conversation_id, now_ts),
			label=f"fb_last_activity:{conversation_id}",
		)
		for sid, member in list(conv.members_active.items()):
			if not member.alive:
				continue
			record = registry.live_blocking_pending(conversation_id, sid)
			if record is not None:
				payload = INTERJECT_PREFIX + text
				if record.notices:
					payload = "\n\n".join([*record.notices, payload])
				popped = await terminate_pending(
					registry, None, logger, record,
					resolve_text=payload, mark_cancelled=False, remember_resolved=True,
				)
				if popped:
					resolved.append(sid)
					to_cancel.append(record.request_id)
		woken = _wake_all_from(conv, exclude_session_ids=set(resolved))
		for sid, member in list(conv.members_active.items()):
			if not member.alive or sid in resolved or sid in woken:
				continue
			if session_registry is not None and session_registry.queue_notice(sid, NOTICE_PREFIX + text):
				noticed.append(sid)
	for request_id in to_cancel:
		try:
			await backend.mark_question_cancelled(conversation_id, request_id)
		except Exception as exc:
			await logger.surface_error(f"inbound_mark_cancelled_failed: conv={conversation_id} req={request_id} {exc}")
	return {"delivered": True, "resolved": resolved, "woken": sorted(woken), "noticed": noticed}


async def deliver_background_answer(registry, backend, session_registry, logger, record, answer_text: str) -> str:
	try:
		await backend.remove_pending_question_record(record.conversation_id, record.request_id)
	except Exception as exc:
		await logger.surface_error(f"background_pending_record_cleanup_failed: {exc}")
	question = record.question or "(question unavailable)"
	payload = f"John answered your earlier question '{question}': {answer_text}"
	sid = record.cli_session_id
	conv = registry.conversations.get(record.conversation_id)
	rung = "queued_notice"
	cancel_request_id = None
	if conv is not None:
		async with conv.lock:
			live = registry.live_blocking_pending(record.conversation_id, sid)
			if live is not None:
				composed = payload
				notices = [*record.notices, *live.notices]
				if notices:
					composed = "\n\n".join([*notices, payload])
				popped = await terminate_pending(
					registry, None, logger, live,
					resolve_text=composed, mark_cancelled=False, remember_resolved=True,
				)
				if popped:
					rung = "resolved_ask"
					cancel_request_id = live.request_id
			if rung != "resolved_ask":
				for entry in list(conv.wait_queue):
					if entry["member"].cli_session_id == sid and not entry["future"].done():
						conv.wait_queue.remove(entry)
						entry["future"].set_result(payload)
						rung = "woke_wait"
						break
	if rung == "resolved_ask" and cancel_request_id is not None:
		try:
			await backend.mark_question_cancelled(record.conversation_id, cancel_request_id)
		except Exception as exc:
			await logger.surface_error(f"inbound_mark_cancelled_failed: conv={record.conversation_id} req={cancel_request_id} {exc}")
	if rung == "queued_notice" and session_registry is not None:
		for notice in record.notices:
			session_registry.queue_notice(sid, notice)
		session_registry.queue_notice(sid, payload)
	await logger.info(
		f"background_answer_delivered: conversation_id={record.conversation_id} "
		f"request_id={record.request_id} rung={rung}"
	)
	return rung
