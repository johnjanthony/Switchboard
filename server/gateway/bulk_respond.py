from __future__ import annotations

import asyncio
from server.messenger import MessageWriter
from server.registry import Registry
from server.logging_jsonl import JsonlLogger

async def _apply_bulk_respond_decision(
	registry: Registry,
	backend: MessageWriter,
	logger: JsonlLogger,
	scope_cwd: str | None,
	decision: str | None,
	default_text: str,
) -> bool:
	"""Apply a bulk-respond decision (sent from the phone via the exit command)
	and return True if the away-mode flip should be committed.

	scope_cwd=None means global scope; otherwise per-cwd. Decision values:
	- "send_default": resolve all pending in scope with `default_text`, commit flip
	- "skip": leave pendings in place, commit flip
	- "cancel": leave pendings in place, do NOT commit flip
	- None: if no pendings, commit; otherwise treat as cancel and surface an error
	"""
	from server.gateway.handlers import _append_session_log
	
	pendings = (
		registry.all_pending() if scope_cwd is None
		else registry.pending_for_cwd(scope_cwd)
	)

	if decision is None:
		if not pendings:
			return True
		await logger.surface_error(
			f"away_mode_commands: bulk_respond_decision_missing "
			f"(scope={scope_cwd!r}, pending={len(pendings)}); treating as cancel"
		)
		return False

	if decision == "send_default":
		# Resolve every pending in scope in parallel: registry.resolve +
		# send_resolution_confirmation + write_channel_message. The reply's
		# attached_to_msg_id links it back to the question; the client uses this
		# to splice the reply directly under its question and to derive the
		# answered-state for the question's RESPONDED badge. Per-pending
		# exceptions are caught so a single failure doesn't abort the fan-out.
		async def _resolve_one(p):
			try:
				req_id = registry.resolve(cwd=p.cwd, sender=p.sender, text=default_text)
				if req_id is None:
					return
				tasks = [
					backend.send_resolution_confirmation(req_id, p.cwd, (p.cwd, p.sender), response_text=default_text),
					backend.write_channel_message(
						p.cwd, "John", "human", default_text,
						attached_to_msg_id=p.msg_id,
					),
				]
				await asyncio.gather(*tasks)
				await _append_session_log(logger.log_path, p.cwd, "←", default_text, logger)
				await logger.notify_sent(p.cwd, f"Bulk Reply: {default_text}")
			except Exception as exc:
				await logger.surface_error(f"bulk_resolve_failed: cwd={p.cwd} sender={p.sender} err={exc}")

		if pendings:
			try:
				await asyncio.gather(*[_resolve_one(p) for p in pendings])
			except Exception as exc:
				await logger.surface_error(
					f"away_mode_commands: bulk_respond_send_default error "
					f"(scope={scope_cwd!r}): {exc}"
				)
		return True

	if decision == "skip":
		return True
	if decision == "cancel":
		return False

	await logger.surface_error(f"away_mode_commands: unknown decision={decision!r}")
	return False
