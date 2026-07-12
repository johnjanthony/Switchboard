from __future__ import annotations

import asyncio
from server.messenger import MessageWriter
from server.registry import Registry
from server.logging_jsonl import JsonlLogger
from server.gateway.parked import finish_parked_resolve

async def _apply_bulk_respond_decision(
	registry: Registry,
	backend: MessageWriter,
	logger: JsonlLogger,
	decision: str | None,
	default_text: str,
	session_registry=None,
) -> bool:
	"""Apply a bulk-respond decision (sent from the phone via the exit command)
	and return True if the away-mode flip should be committed.

	Operates on the global pending set only. Decision values:
	- "send_default": resolve all pendings with `default_text`, commit flip
	- "skip": leave pendings in place, commit flip
	- "cancel": leave pendings in place, do NOT commit flip
	- None: if no pendings, commit; otherwise treat as cancel and surface an error
	"""
	from server.gateway.handlers import _append_session_log

	pendings = registry.all_pending()

	if decision is None:
		if not pendings:
			return True
		await logger.surface_error(
			f"away_mode_commands: bulk_respond_decision_missing "
			f"(pending={len(pendings)}); treating as cancel"
		)
		return False

	if decision == "send_default":
		if not default_text.strip():
			await logger.surface_error(
				"away_mode_commands: send_default with blank default_text; treating as cancel (M07)"
			)
			return False
		# Resolve every pending in scope in parallel: registry.resolve +
		# write_channel_message. The reply's attached_to_msg_id links it back
		# to the question; the client uses this to splice the reply directly
		# under its question and to derive the answered-state for the
		# question's RESPONDED badge. Per-pending exceptions are caught so a
		# single failure doesn't abort the fan-out.
		async def _resolve_one(p):
			try:
				req_id = registry.resolve(p.conversation_id, p.request_id, default_text)
				if req_id is None:
					return
				if p.future is None:
					await finish_parked_resolve(backend, session_registry, logger, p, default_text)
				await backend.write_conversation_message(
					p.conversation_id, "John", "human", default_text,
					attached_to_msg_id=p.msg_id,
				)
				await _append_session_log(logger.log_path, p.conversation_id, "←", default_text, logger)
				await logger.notify_sent(p.conversation_id, f"Bulk Reply: {default_text}")
			except Exception as exc:
				await logger.surface_error(f"bulk_resolve_failed: conversation_id={p.conversation_id} sender={p.sender} err={exc}")

		if pendings:
			try:
				await asyncio.gather(*[_resolve_one(p) for p in pendings])
			except Exception as exc:
				await logger.surface_error(
					f"away_mode_commands: bulk_respond_send_default error: {exc}"
				)
		return True

	if decision == "skip":
		return True
	if decision == "cancel":
		return False

	await logger.surface_error(f"away_mode_commands: unknown decision={decision!r}")
	return False
