from __future__ import annotations

import asyncio
from typing import Any
from server.registry import Registry
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
from server.gateway.bg_tasks import _spawn_bg

_LOOP_CRASH_ALERT_THRESHOLD = 5
_LOOP_BACKOFF_MAX = 60.0

async def _loop_crash_backoff(
	backend: Any,
	logger: JsonlLogger,
	label: str,
	consecutive_failures: int,
	backoff: float,
	exc: Exception,
) -> float:
	"""Log a dispatch-loop crash, escalate to the admin channel after the
	threshold is hit, sleep `backoff` seconds, and return the next backoff."""
	await logger.surface_error(
		f"{label}_loop_crashed: {exc} (count={consecutive_failures}, sleep={backoff:.1f}s)"
	)
	if consecutive_failures == _LOOP_CRASH_ALERT_THRESHOLD:
		try:
			await backend.send_text(
				f"Switchboard {label} loop has failed {consecutive_failures} times in a row — check service logs."
			)
		except Exception as alert_exc:
			await logger.surface_error(f"{label}_loop_alert_failed: {alert_exc}")
	await asyncio.sleep(backoff)
	return min(backoff * 2, _LOOP_BACKOFF_MAX)

async def dispatch_responses(
	registry: Registry,
	backend: MessengerBackend,
	logger: JsonlLogger,
) -> None:
	from server.gateway.handlers import _append_session_log
	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for response in backend.poll_responses():
				consecutive_failures = 0
				backoff = 1.0
				try:
					corr = response.correlation
					if isinstance(corr, tuple) and len(corr) == 2:
						cwd, sender = corr
						record = registry.get((cwd, sender))
						req_id = registry.resolve(cwd=cwd, sender=sender, text=response.text)
						if req_id is None:
							await logger.surface_error(f"unknown_correlation: cwd={cwd} sender={sender}")
							try:
								await backend.send_stale_reply_notice(cwd, sender)
							except Exception as exc:
								await logger.surface_error(f"stale_reply_notice_failed: {exc}")
						elif record is not None:
							if record.msg_id and hasattr(backend, "write_response_text"):
								# Update original question so it stays answered across restarts
								_spawn_bg(
									backend.write_response_text(cwd, record.msg_id, response.text),
									label=f"response_update:{cwd}:{record.msg_id}",
								)
							# Add a NEW message to the history so it shows up in-line in the app
							async def _write_history(cid=cwd, txt=response.text):
								try:
									await backend.write_channel_message(cid, "John", "human", txt)
									await logger.notify_sent(cid, f"Reply: {txt}")
									await _append_session_log(logger.log_path, cid, "←", txt, logger)
								except Exception as exc:
									await logger.surface_error(f"history_write_failed: {exc}")
							_spawn_bg(_write_history(), label=f"history_write:{cwd}")
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
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "dispatch_responses", consecutive_failures, backoff, exc,
			)

async def _safe_handle(spawn_handler: Any, raw: str, logger: JsonlLogger) -> None:
	try:
		await spawn_handler.handle(raw)
	except asyncio.CancelledError:
		raise
	except Exception as exc:
		await logger.surface_error(f"dispatch_commands_error: {exc}")


async def dispatch_commands(
	spawn_handler: Any,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for raw in backend.poll_commands():
				consecutive_failures = 0
				backoff = 1.0
				_spawn_bg(_safe_handle(spawn_handler, raw, logger), label="dispatch_commands")

		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "dispatch_commands", consecutive_failures, backoff, exc,
			)

async def dispatch_away_mode_commands(
	registry: Registry,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	"""Consume away_mode_commands queue entries and dispatch to registry/bulk-respond.

	Decisions about pending questions arrive as fields on the exit_global / exit_cwd
	command (set by the phone after showing its dialog). The server applies the
	decision via `_apply_bulk_respond_decision` and only flips the away-mode mirror
	if the decision allows commit."""
	from server.canonicalization import canonicalize_cwd, CanonicalizationError
	from server.gateway.bulk_respond import _apply_bulk_respond_decision

	poll = getattr(backend, "poll_away_mode_commands", None)
	if poll is None:
		return

	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for cmd in poll():
				consecutive_failures = 0
				backoff = 1.0
				cmd_type = cmd.get("type", "")
				try:
					if cmd_type == "enter_global":
						await backend.write_away_mode_mirror(None, True)
						await logger.info(f"away_mode_commands: enter_global applied")

					elif cmd_type == "exit_global":
						decision = cmd.get("decision")
						default_text = cmd.get("default_text", "")
						commit = await _apply_bulk_respond_decision(
							registry, backend, logger,
							scope_cwd=None, decision=decision, default_text=default_text,
						)
						if commit:
							await backend.write_away_mode_mirror(None, False)
						await logger.info(
							f"away_mode_commands: exit_global applied "
							f"(decision={decision}, commit={commit})"
						)

					elif cmd_type == "enter_cwd":
						raw_cwd = cmd.get("cwd") or ""
						try:
							canonical = canonicalize_cwd(raw_cwd)
						except CanonicalizationError as exc:
							await logger.surface_error(f"away_mode_commands: enter_cwd bad cwd={raw_cwd!r} {exc}")
							continue
						await backend.write_away_mode_mirror(canonical, True)
						await logger.info(f"away_mode_commands: enter_cwd {canonical}")

					elif cmd_type == "exit_cwd":
						raw_cwd = cmd.get("cwd") or ""
						try:
							canonical = canonicalize_cwd(raw_cwd)
						except CanonicalizationError as exc:
							await logger.surface_error(f"away_mode_commands: exit_cwd bad cwd={raw_cwd!r} {exc}")
							continue
						decision = cmd.get("decision")
						default_text = cmd.get("default_text", "")
						commit = await _apply_bulk_respond_decision(
							registry, backend, logger,
							scope_cwd=canonical, decision=decision, default_text=default_text,
						)
						if commit:
							await backend.write_away_mode_mirror(canonical, False)
						await logger.info(
							f"away_mode_commands: exit_cwd {canonical} applied "
							f"(decision={decision}, commit={commit})"
						)

					else:
						await logger.surface_error(f"away_mode_commands: unknown type={cmd_type!r}")

				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"away_mode_commands_dispatch_error: {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "away_mode_commands", consecutive_failures, backoff, exc,
			)

async def dispatch_inject_queue(
	registry: Registry,
	backend: Any,
	logger: JsonlLogger,
) -> None:
	"""Deliver human inject messages from the Android compose box to collab sessions."""
	poll = getattr(backend, "poll_inject_messages", None)
	if poll is None:
		return
	consecutive_failures = 0
	backoff = 1.0
	while True:
		try:
			async for session_id, inject_id, text in poll():
				consecutive_failures = 0
				backoff = 1.0
				try:
					session = registry.get_session(session_id)
					if session is None:
						await logger.surface_error(f"inject_unknown_session: {session_id} inject_id={inject_id}")
					else:
						session.deliver_inject(text)
				except asyncio.CancelledError:
					raise
				except Exception as exc:
					await logger.surface_error(f"inject_dispatch_error: inject_id={inject_id} {exc}")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			consecutive_failures += 1
			backoff = await _loop_crash_backoff(
				backend, logger, "inject_queue", consecutive_failures, backoff, exc,
			)
