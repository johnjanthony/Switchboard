from __future__ import annotations

import asyncio
from typing import Any
from server.registry import Registry
from server.logging_jsonl import JsonlLogger
from server.messenger import MessengerBackend
from server.gateway.bg_tasks import _spawn_bg
from server.firebase_supervisor import LoopSupervisor

async def dispatch_responses(
	registry: Registry,
	backend: MessengerBackend,
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
						cwd, sender = corr
						record = registry.get((cwd, sender))
						req_id = registry.resolve(cwd=cwd, sender=sender, text=response.text)
						if req_id is None:
							await logger.surface_error(f"unknown_correlation: cwd={cwd} sender={sender}")
							try:
								await backend.send_stale_reply_notice(cwd, sender)
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
							if record.msg_id and hasattr(backend, "write_response_text"):
								# Update original question so it stays answered across restarts
								_spawn_bg(
									backend.write_response_text(cwd, record.msg_id, response.text),
									label=f"response_update:{cwd}:{record.msg_id}",
								)
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
			await supervisor.record_crash(exc)

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
	supervisor: LoopSupervisor,
) -> None:
	while True:
		try:
			async for raw in backend.poll_commands():
				supervisor.record_success()
				_spawn_bg(_safe_handle(spawn_handler, raw, logger), label="dispatch_commands")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			await supervisor.record_crash(exc)

async def _clear_all_cwd_overrides(registry: Registry, backend: Any, logger: JsonlLogger) -> int:
	"""Wipe every per-channel away_mode override.

	Invoked after a successful global enter/exit so that flipping the global flag
	resets per-channel state — the user's mental model is that a global toggle is
	a Big Hammer and channel-level overrides should not survive it. Returns the
	number of overrides cleared.

	Sources its key list from the registry's in-memory cache rather than re-
	reading channels from Firebase: the cache is the live mirror, walking it is
	cheap, and writing one delete per known override avoids a `channels/` shallow
	read on every global flip.
	"""
	overrides = registry.cwd_overrides()
	if not overrides:
		return 0
	for cwd in list(overrides.keys()):
		try:
			await backend.write_away_mode_mirror(cwd, None)
		except Exception as exc:
			await logger.surface_error(
				f"away_mode_commands: clear_override_failed cwd={cwd!r} {exc}"
			)
	return len(overrides)


async def dispatch_away_mode_commands(
	registry: Registry,
	backend: Any,
	logger: JsonlLogger,
	supervisor: LoopSupervisor,
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

	while True:
		try:
			async for cmd in poll():
				supervisor.record_success()
				cmd_type = cmd.get("type", "")
				try:
					if cmd_type == "enter_global":
						await backend.write_away_mode_mirror(None, True)
						cleared = await _clear_all_cwd_overrides(registry, backend, logger)
						await logger.info(
							f"away_mode_commands: enter_global applied (cleared_overrides={cleared})"
						)

					elif cmd_type == "exit_global":
						decision = cmd.get("decision")
						default_text = cmd.get("default_text", "")
						commit = await _apply_bulk_respond_decision(
							registry, backend, logger,
							scope_cwd=None, decision=decision, default_text=default_text,
						)
						cleared = 0
						if commit:
							await backend.write_away_mode_mirror(None, False)
							cleared = await _clear_all_cwd_overrides(registry, backend, logger)
						await logger.info(
							f"away_mode_commands: exit_global applied "
							f"(decision={decision}, commit={commit}, cleared_overrides={cleared})"
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
			await supervisor.record_crash(exc)

async def dispatch_inject_queue(
	registry: Registry,
	backend: Any,
	logger: JsonlLogger,
	supervisor: LoopSupervisor,
) -> None:
	"""Deliver human inject messages from the Android compose box to collab sessions."""
	poll = getattr(backend, "poll_inject_messages", None)
	if poll is None:
		return
	while True:
		try:
			async for session_id, inject_id, text in poll():
				supervisor.record_success()
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
			await supervisor.record_crash(exc)
