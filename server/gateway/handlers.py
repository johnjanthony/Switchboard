from __future__ import annotations

import asyncio
import json
import uuid
from functools import wraps

import anyio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.messenger import MessageWriter, ChannelLifecycle, ConversationStore
from server.rate_limiter import RateLimiter
from server.registry import Registry
from server.gateway.document import _validate_path, _sha256_hex
from server.gateway.bg_tasks import _spawn_bg

TIMEOUT_SENTINEL = "__TIMEOUT__"

def _envelope(status: str, **fields) -> str:
	"""One-line JSON status envelope for conversation-tool returns. Internal
	protocol keeps carrying plain strings and sentinels; this is the MCP-facing
	boundary only. None-valued fields are omitted so envelopes stay minimal."""
	payload: dict = {"status": status}
	for key, value in fields.items():
		if value is not None:
			payload[key] = value
	return json.dumps(payload)


def _terminal_envelope(text: str) -> str | None:
	"""Map an internal terminal sentinel to its envelope, or None for normal text.
	Futures resolved by force-end, combine, and timeout paths carry these strings;
	translating here (not at the resolution sites) keeps every internal consumer
	(wait-queue drains, logs, tests of internals) on the stable string protocol."""
	if not isinstance(text, str):
		return None
	if text == TIMEOUT_SENTINEL:
		return _envelope("timeout")
	if text.startswith("__CONVERSATION_ENDED__"):
		cause = "ended"
		lines = text.splitlines()
		if len(lines) > 1 and lines[1].startswith("(") and lines[1].endswith(")"):
			cause = lines[1][1:-1]
		return _envelope("conversation_ended", cause=cause)
	return None


def _wrap_wait_result(conversation_id: str, text: str) -> str:
	"""Envelope a message_and_await wake result. Internal wake payloads are plain
	strings (delta logs, dormancy notices, lobby 'ok. open_conversation' wakes);
	sentinels map to their terminal envelopes."""
	terminal = _terminal_envelope(text)
	if terminal is not None:
		return terminal
	if text.startswith("__CONVERSATION_EMPTY__"):
		partings = text.partition("\n")[2]
		return _envelope("conversation_empty", conversation_id=conversation_id, log=partings or None)
	if text.startswith("ok. open_conversation = "):
		# Lobby-hold wake (open-marker conv, deprecation window): the meaningful
		# part is the peer-joined line, not the legacy prefix.
		log = text.partition("\n")[2]
		return _envelope("ok", conversation_id=conversation_id, log=log or None)
	return _envelope("ok", conversation_id=conversation_id, log=text or None)

_SESSION_START = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()

def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]

def _locate_member(conv, cli_session_id: str):
	"""Return the active member for this cli_session_id, or None."""
	return conv.members_active.get(cli_session_id) if conv is not None else None

def _canonical_sender(registry, conversation_id: str, cli_session_id: str, raw_sender: str) -> str:
	"""The sender name a message/pending should be attributed under.

	Prefer the member's disambiguated sender (e.g. 'Claude Win 2') over the raw
	agent-supplied name: two agents that both call themselves 'Claude Win' are
	distinct members but would otherwise collide on the (conversation_id, sender)
	pending key and cancel each other's questions. Falls back to the raw sender
	when the member isn't resolvable (session bound to an unloaded conversation)."""
	member = _locate_member(registry.conversations.get(conversation_id), cli_session_id)
	return member.sender if member is not None else raw_sender

async def _append_session_log(log_path: str, conversation_id: str, direction: str, text: str, logger: JsonlLogger) -> None:
	# conversation_id is a server-minted `conv-<uuid>` string — already filesystem-safe,
	# no Firebase-key sanitization needed.
	path = Path(log_path).parent / "sessions" / f"{conversation_id}_{_SESSION_START}.log"
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	line = f"{ts} {direction} {text}\n"

	await logger.info(f"writing_session_log: {path.absolute()}")

	def _write() -> None:
		path.parent.mkdir(exist_ok=True)
		with path.open("a", encoding="utf-8") as f:
			f.write(line)

	await asyncio.to_thread(_write)


async def _safe_mark_cancelled(backend: MessageWriter, conversation_id: str, request_id: str, logger: JsonlLogger) -> None:
	try:
		await backend.mark_question_cancelled(conversation_id, request_id)
	except Exception as exc:
		await logger.surface_error(f"mark_cancelled_failed: {exc}")

def require_cli_session_id(handler):
	"""Decorator: rejects calls missing cli_session_id.

	The switchboard MCP plugin's PreToolUse hook injects cli_session_id (and
	cwd) into every switchboard tool call. A missing cli_session_id means
	either (a) the hook didn't fire (older or non-plugin Claude install), or
	(b) the call originated from a non-Claude agent (e.g., Gemini). Under the
	v2 routing model (parent design), switchboard tools require this — the
	channel-by-cwd legacy routing has been retired.

	The decorator extracts cli_session_id + cwd from kwargs, checks
	cli_session_id is non-empty, and forwards both to the handler.
	"""

	@wraps(handler)
	async def wrapped(*args, cli_session_id: str | None = None, cwd: str | None = None, **kwargs):
		if not cli_session_id:
			return (
				"ERROR: cli_session_id required. This call appears to come from a "
				"Claude session without the switchboard plugin's PreToolUse hook "
				"installed, or from a non-Claude agent. Switchboard tools require "
				"hook-injected session_id under the v2 routing model."
			)
		return await handler(*args, cli_session_id=cli_session_id, cwd=cwd, **kwargs)

	return wrapped


@dataclass
class ToolHandlers:
	ask_human: Callable[..., Coroutine[None, None, str]]
	notify_human: Callable[..., Coroutine[None, None, str]]
	send_document_human: Callable[..., Coroutine[None, None, str]]
	message_and_await_agent: Callable[..., Coroutine[None, None, str]]
	open_conversation: Callable[..., Coroutine[None, None, str]]
	enter_conversation: Callable[..., Coroutine[None, None, str]]
	lookup_conversation_ids: Callable[..., Coroutine[None, None, str]]
	leave_conversation: Callable[..., Coroutine[None, None, str]]
	set_away_mode: Callable[..., Coroutine[None, None, str]]
	combine_conversations: Callable[..., Coroutine[None, None, str]]
	join_conversation: Callable[..., Coroutine[None, None, str]]
	handle_agent_status: Callable[..., Coroutine[None, None, None]]

class _ToolHandlersBackend(MessageWriter, ChannelLifecycle, ConversationStore):
	"""Backend surface used by build_tool_handlers (and its closures)."""

def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: _ToolHandlersBackend,
	logger: JsonlLogger,
	limiter: RateLimiter | None = None,
	session_registry=None,
) -> ToolHandlers:
	def _validate_sender(sender: str) -> str | None:
		if "__" in sender:
			return f"ERROR: sender name '{sender}' contains restricted characters '__'."
		return None

	def _rate_limit_error() -> str:
		return (
			f"ERROR: rate limit exceeded — you are sending too fast.\n"
			f"Limit is {limiter.rate_per_minute} messages/min per conversation.\n"
			f"Wait at least {limiter.wait_seconds} seconds before retrying, or slow your notify cadence."
		)

	def _touch_sessions(handler):
		# MCP-call safety net: any switchboard call from a session the registry
		# has never seen (old plugin, missed SessionStart) upserts it, and every
		# call refreshes cwd - the registry's only cwd source besides SessionStart.
		# Sender is NOT extracted here: its positional slot varies per tool (it is
		# args[0] for message_and_await_agent but args[1] for ask_human), so the
		# registry learns the display name from the membership paths instead.
		@wraps(handler)
		async def wrapped(*args, cli_session_id=None, cwd=None, **kwargs):
			if cli_session_id and session_registry is not None:
				session_registry.touch_mcp(cli_session_id, cwd=cwd or "")
			return await handler(*args, cli_session_id=cli_session_id, cwd=cwd, **kwargs)
		return wrapped

	@require_cli_session_id
	@_touch_sessions
	async def notify_human(
		message: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		from server.conversation_ops import _resolve_conversation_and_member
		conversation_id = await _resolve_conversation_and_member(
			registry, cli_session_id, cwd, sender, backend=backend,
		)
		sender = _canonical_sender(registry, conversation_id, cli_session_id, sender)
		if limiter is not None and not limiter.consume(conversation_id):
			await logger.rate_limited(conversation_id, "notify_human")
			return _rate_limit_error()
		try:
			await backend.write_conversation_message(conversation_id, sender, "notify", message, format=format, title=title)
			await logger.notify_sent(conversation_id, message)
			await _append_session_log(config.log_path, conversation_id, "→", message, logger)
			if not registry.global_away_mode:
				# R1 (decided 2026-06-11): the notification is delivered either
				# way, but at-desk the terminal is the canonical surface. The
				# sentinel tells the agent to route remaining output there.
				# This is routing guidance, not a failure.
				return "ERROR: John is at his desk (notification delivered to phone anyway)."
			return "ok"
		except Exception as exc:
			await logger.tool_error(None, conversation_id, str(exc))
			return f"ERROR: {exc}"

	@require_cli_session_id
	@_touch_sessions
	async def ask_human(
		question: str,
		sender: str,
		title: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		# Defensive (T-145): a session still bound to an Ended conversation (the
		# race window after force-end resolved its future but before
		# session-fallback rebinds it) must not register a new pending question
		# or mint orphan state. Return the terminal sentinel so the agent stops
		# instead of re-stranding or re-adding itself to a dead conversation.
		bound_id = registry.session_to_conversation_id.get(cli_session_id)
		if bound_id is not None:
			bound_conv = registry.conversations.get(bound_id)
			if bound_conv is not None and bound_conv.state == "ended":
				return _envelope("conversation_ended", cause="force-ended")
		from server.conversation_ops import _resolve_conversation_and_member
		conversation_id = await _resolve_conversation_and_member(
			registry, cli_session_id, cwd, sender, backend=backend,
		)
		# Attribute the question (and key the pending) under the member's
		# disambiguated sender, not the raw agent-supplied name, so two agents
		# sharing a name don't collide on the pending key and cancel each other.
		sender = _canonical_sender(registry, conversation_id, cli_session_id, sender)

		if limiter is not None and not limiter.consume(conversation_id):
			await logger.rate_limited(conversation_id, "ask_human")
			return _rate_limit_error()

		# At-desk redirect: when global away mode is OFF, John is at his desk
		# watching the terminal. Don't block the agent for 24h — write the
		# question to the phone as a one-way notify (so it's still surfaced)
		# and return the documented sentinel so the agent can repeat the
		# question in the terminal where John is actually watching.
		if not registry.global_away_mode:
			try:
				await backend.write_conversation_message(
					conversation_id, sender, "notify", question,
					format=format, title=title,
				)
				await logger.notify_sent(conversation_id, question)
				await _append_session_log(config.log_path, conversation_id, "→", question, logger)
			except Exception as exc:
				await logger.tool_error(None, conversation_id, str(exc))
				# Even if the Firebase write fails, return the at-desk sentinel:
				# the agent's next action should still be to ask John in the
				# terminal, not to surface a backend error.
			return "ERROR: John is at his desk. Ask this question via the terminal."

		request_id = _new_request_id()
		started = datetime.now(timezone.utc)
		correlation = None
		try:
			correlation, msg_id = await backend.write_conversation_message(
				conversation_id, sender, "question", question,
				request_id=request_id, format=format, suggestions=suggestions, title=title,
			)
			future, prior_request_id = registry.add(
				conversation_id=conversation_id, cli_session_id=cli_session_id, sender=sender,
				request_id=request_id, msg_id=msg_id, return_superseded=True,
			)
			if prior_request_id is not None:
				await _safe_mark_cancelled(backend, conversation_id, prior_request_id, logger)
			# Persist pending_questions record per 2026-05-19 spec lines 349-355
			_spawn_bg(
				backend.add_pending_question_record(
					conversation_id, request_id,
					sender=sender, msg_id=msg_id,
					question_text=question, suggestions=suggestions,
				),
				label=f"fb_add_pending_question:{conversation_id}:{request_id}",
			)
			await logger.request_created(request_id, conversation_id, question)
			await _append_session_log(config.log_path, conversation_id, "→", question, logger)
		except asyncio.CancelledError:
			# Shield cleanup against re-cancellation: MCP's responder.cancel()
			# leaves the surrounding anyio CancelScope in a sustained cancelled
			# state, so any subsequent await is also a checkpoint that re-raises
			# CancelledError. Without this shield, the Firebase write below
			# never completes and the question stays cancelled=false.
			with anyio.CancelScope(shield=True):
				registry.remove(conversation_id, cli_session_id, request_id=request_id)
				await _safe_mark_cancelled(backend, conversation_id, request_id, logger)
			raise
		except Exception as exc:
			await logger.tool_error(request_id, conversation_id, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
		except asyncio.TimeoutError:
			await logger.timeout(request_id, conversation_id, config.timeout_seconds)
			registry.remove(conversation_id, cli_session_id, request_id=request_id)
			# Mark the question message cancelled, which also removes the
			# pending_questions record (firebase.py). The phone derives
			# "pending" purely from message flags, so without the cancelled
			# flag a timed-out question stays pending on the phone forever (H05).
			_spawn_bg(
				backend.mark_question_cancelled(conversation_id, request_id),
				label=f"fb_mark_cancelled:timeout:{conversation_id}:{request_id}",
			)
			try:
				await backend.send_timeout_followup(
					request_id, conversation_id, config.timeout_seconds, correlation,
				)
			except Exception as exc:
				await logger.surface_error(f"timeout_followup_failed: {exc}", correlation=str(correlation))
			return _envelope("timeout")
		except asyncio.CancelledError:
			# See note above re: shielding. This is the live cancel-from-MCP
			# path that the user observes when pressing Esc on the tool call.
			with anyio.CancelScope(shield=True):
				registry.remove(conversation_id, cli_session_id, request_id=request_id)
				await _safe_mark_cancelled(backend, conversation_id, request_id, logger)
			raise
		except Exception as exc:
			await logger.tool_error(request_id, conversation_id, str(exc))
			registry.remove(conversation_id, cli_session_id, request_id=request_id)
			_spawn_bg(
				backend.mark_question_cancelled(conversation_id, request_id),
				label=f"fb_mark_cancelled:error:{conversation_id}:{request_id}",
			)
			return f"ERROR: {exc}"

		# Force-end resolves the pending future with the __CONVERSATION_ENDED__
		# sentinel (T-145) rather than cancelling it. That is a terminal signal,
		# not an answer: hand it straight back so the agent stops without
		# retrying, and skip the answered-path side effects (resolution
		# confirmation, pending-record removal). handle_force_end already marked
		# the question cancelled in Firebase.
		if isinstance(result, str) and result.startswith("__CONVERSATION_ENDED__"):
			return _terminal_envelope(result)

		# Successful resolution: clear the pending_questions record (read by
		# the startup sweep). answered_question_msg_ids is NOT written: the
		# phone derives answered-state from message flags, so that subtree had
		# no reader (F-66/F-73, decided 2026-06-13).
		_spawn_bg(
			backend.remove_pending_question_record(conversation_id, request_id),
			label=f"fb_remove_pending_question:resolved:{conversation_id}:{request_id}",
		)

		await _append_session_log(config.log_path, conversation_id, "←", result, logger)
		duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
		# correlation is always a plain conv-<uuid> string post-D4, so source
		# classification never resolves to anything but "unknown"; inline it.
		await logger.request_resolved(
			request_id, conversation_id, response_text=result, source="unknown", duration_ms=duration_ms,
		)
		try:
			await backend.send_resolution_confirmation(request_id, conversation_id, correlation, response_text=result)
		except Exception as exc:
			await logger.surface_error(f"resolution_confirmation_failed: {exc}", correlation=str(correlation))
		return result

	@require_cli_session_id
	@_touch_sessions
	async def send_document_human(
		path: str,
		sender: str,
		title: str | None = None,
		caption: str | None = None,
		*,
		cli_session_id: str,
		cwd: str,
		_cwd_path: Path | None = None,
	) -> str:
		if err := _validate_sender(sender):
			return err
		from server.conversation_ops import _resolve_conversation_and_member
		conversation_id = await _resolve_conversation_and_member(
			registry, cli_session_id, cwd, sender, backend=backend,
		)
		sender = _canonical_sender(registry, conversation_id, cli_session_id, sender)
		# Path validation: resolve against the agent's actual filesystem cwd.
		cwd_path = _cwd_path if _cwd_path is not None else Path(cwd)
		try:
			resolved = _validate_path(path, cwd=cwd_path)
		except ValueError as exc:
			await logger.tool_error(None, conversation_id, str(exc))
			return f"ERROR: {exc}"

		if limiter is not None and not limiter.consume(conversation_id):
			await logger.rate_limited(conversation_id, "send_document_human")
			return _rate_limit_error()

		try:
			size_bytes = resolved.stat().st_size
			sha256 = await _sha256_hex(resolved)
			await backend.write_conversation_message(
				conversation_id, sender, "document",
				caption or resolved.name,
				url=str(resolved),
				filename=resolved.name,
				title=title,
			)
		except Exception as exc:
			await logger.tool_error(None, conversation_id, str(exc))
			return f"ERROR: {exc}"

		try:
			await logger.document_sent(conversation_id, str(resolved), size_bytes, sha256, caption)
		except Exception as exc:
			await logger.surface_error(f"document_audit_failed: {exc}")
		entry = f"[document: {resolved.name}] {caption}" if caption else f"[document: {resolved.name}]"
		await _append_session_log(config.log_path, conversation_id, "→", entry, logger)
		return "ok"

	@require_cli_session_id
	@_touch_sessions
	async def message_and_await_agent(
		sender: str,
		message: str | None = None,
		title: str | None = None,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		if not message:
			return "ERROR: message is required. The 'listen without speaking' use case is join_conversation()."

		from server.conversation_ops import _resolve_conversation_and_member
		conversation_id = await _resolve_conversation_and_member(
			registry, cli_session_id, cwd, sender, backend=backend, mint_if_unbound=False,
		)
		if conversation_id is None:
			return "ERROR: not in any conversation. End your turn."
		conv = registry.conversations.get(conversation_id)
		if conv is None:
			return "ERROR: bound conversation no longer exists."

		caller_member = conv.members_active.get(cli_session_id)
		if caller_member is None:
			return "ERROR: session bound to conversation but not a member."

		from server.conversation_ops import _wake_one_from
		from server.session_fallback import apply_fallback
		import time

		wait_entry = None
		empty_result: str | None = None
		lobby_wait = False
		async with conv.lock:
			# Append speak event to the conversation log
			now_ts = time.time()
			speak_msg = {
				"seq": len(conv.messages),
				"sender": caller_member.sender,
				"type": "agent_msg",
				"text": message,
				"timestamp": datetime.now(timezone.utc).isoformat(),
				"title": title,
			}
			conv.messages.append(speak_msg)
			conv.last_activity_at = now_ts
			if title is not None:
				conv.title = title
				_spawn_bg(
					backend.write_conversation_title(conversation_id, title),
					label=f"fb_write_title:{conversation_id}",
				)

			# Write to /conversations/<id>/messages
			_spawn_bg(
				backend.write_conversation_message(
					conversation_id, caller_member.sender, "agent_msg", message,
					format="markdown", title=title,
				),
				label=f"fb_write_agent_msg:{conversation_id}:{caller_member.sender}",
			)
			_spawn_bg(
				backend.set_conversation_last_activity(conversation_id, now_ts),
				label=f"fb_last_activity:{conversation_id}",
			)

			# Count alive peers (excluding caller)
			alive_peers = [
				m for m in conv.members_active.values()
				if m.alive and m.cli_session_id != cli_session_id
			]
			if not alive_peers:
				# Sole alive member. Two sub-paths:
				# (a) Conv IS the open marker → hold the lobby. Don't auto-leave;
				#     fall through to a block on conv.open_peer_future after the
				#     lock releases. Opener wakes when a peer joins, or gets
				#     __TIMEOUT__ (without ending the conv) so they can poll again.
				# (b) Otherwise → original auto-leave behavior: caller removed,
				#     conv Ended if no remaining members, mirroring leave_conversation.
				if registry.open_conversation_id == conversation_id:
					lobby_wait = True
					# Mark the speak as seen so the next wake's delta starts from here
					caller_member.last_seen_seq = len(conv.messages)
				else:
					partings = [
						m for m in conv.messages[caller_member.last_seen_seq:]
						if m.get("type") == "parting"
					]
					caller_member.last_seen_seq = len(conv.messages)
					if partings:
						parting_text = "\n".join(f"{p['sender']}: {p['text']}" for p in partings)
						empty_result = f"__CONVERSATION_EMPTY__\n{parting_text}"
					else:
						empty_result = "__CONVERSATION_EMPTY__"

					# Remove caller from members_active; record in history
					caller_member.left_at = now_ts
					old_sender = caller_member.sender
					del conv.members_active[cli_session_id]
					conv.members_history.append(caller_member)
					# Persist members_history entry so it survives restart
					_spawn_bg(
						backend.write_conversation_member_history(conversation_id, caller_member),
						label=f"fb_write_member_history:{conversation_id}:{caller_member.sender}",
					)

					# Terminal-state check (same logic as leave_conversation)
					has_dormant = any(not m.alive for m in conv.members_active.values())
					has_alive = any(m.alive for m in conv.members_active.values())
					conv_ended = not has_alive and not has_dormant
					open_cleared = False
					if conv_ended:
						conv.state = "ended"
						conv.ended_at = now_ts
						if registry.open_conversation_id == conversation_id:
							registry.open_conversation_id = None
							open_cleared = True

					_spawn_bg(
						backend.remove_conversation_member(conversation_id, old_sender),
						label=f"fb_remove_member:{conversation_id}:{old_sender}",
					)
					_spawn_bg(
						backend.set_conversation_last_activity(conversation_id, now_ts),
						label=f"fb_last_activity:{conversation_id}",
					)
					if conv_ended:
						_spawn_bg(
							backend.set_conversation_state(conversation_id, "ended"),
							label=f"fb_set_state:{conversation_id}:ended",
						)
					if open_cleared:
						_spawn_bg(
							backend.set_open_conversation_id(None),
							label=f"fb_clear_open_id:{conversation_id}",
						)
					# Fall through past the lock to apply session-fallback.
			else:
				# Wake FIFO-oldest waiter (if any)
				_wake_one_from(conv)

				# Enqueue caller
				future = asyncio.get_event_loop().create_future()
				wait_entry = {
					"member": caller_member,
					"future": future,
					"waiting_kind": "msg_and_await",
					"block_position": time.monotonic(),
				}
				conv.wait_queue.append(wait_entry)
				caller_member.last_seen_seq = len(conv.messages)

		# If sole-alive-member EMPTY path was taken, apply session-fallback
		# OUTSIDE the conv.lock (it may touch other conversations) and return.
		if empty_result is not None:
			apply_fallback(registry, cli_session_id, backend=backend)
			return _wrap_wait_result(conversation_id, empty_result)

		# Sole-alive in the open-marker conv: hold the lobby until a peer joins
		# or we time out. end_conv_on_timeout=False so the established conv
		# survives a quiet round — caller can poll again or leave_conversation.
		if lobby_wait:
			from server.conversation_ops import _queue_for_open_peer
			lobby_result = await _queue_for_open_peer(
				registry, conversation_id, cli_session_id,
				config.timeout_seconds, backend=backend,
				end_conv_on_timeout=False,
			)
			return _wrap_wait_result(conversation_id, lobby_result)

		# Lock released; now wait
		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
			return _wrap_wait_result(conversation_id, result)
		except asyncio.TimeoutError:
			async with conv.lock:
				if wait_entry in conv.wait_queue:
					conv.wait_queue.remove(wait_entry)
			return _envelope("timeout")
		except asyncio.CancelledError:
			async with conv.lock:
				if wait_entry in conv.wait_queue:
					conv.wait_queue.remove(wait_entry)
			raise

	@require_cli_session_id
	@_touch_sessions
	async def open_conversation(
		sender: str,
		title: str | None = None,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		conv_id = registry.session_to_conversation_id.get(cli_session_id)
		if conv_id is None:
			# Caller isn't in any conversation yet. Mint one, promote it, and
			# block until a peer joins. Atomic bootstrap — the wait IS the API,
			# so the opener doesn't have to know about intro-queue mechanics.
			from server.conversation_ops import (
				_create_active_conversation_for,
				_queue_for_open_peer,
			)
			conv_id = await _create_active_conversation_for(
				registry, cli_session_id, cwd, sender, backend=backend, title=title,
			)
			registry.open_conversation_id = conv_id
			_spawn_bg(
				backend.set_open_conversation_id(conv_id),
				label=f"fb_set_open_conv_id:{conv_id}",
			)
			await logger.info(f"open_conversation: minted+promoted conv_id={conv_id} sender={sender}")
			return await _queue_for_open_peer(
				registry, conv_id, cli_session_id, config.timeout_seconds, backend=backend,
			)
		conv = registry.conversations.get(conv_id)
		if conv is None:
			return "ERROR: bound conversation no longer exists."
		if conv.state != "active":
			# Don't promote an Ended conversation to the open singleton (F-72):
			# an Ended conv has no members and joiners would block forever.
			return "ERROR: cannot open an ended conversation."
		# Ensure the (possibly fresh-spawn-bound, member-less) caller is a member
		# before the rename/open logic, which otherwise silently finds no member.
		from server.conversation_ops import _resolve_conversation_and_member
		await _resolve_conversation_and_member(registry, cli_session_id, cwd, sender, backend=backend)
		async with conv.lock:
			if title:
				conv.title = title
				_spawn_bg(
					backend.write_conversation_title(conv_id, title),
					label=f"fb_write_title:{conv_id}",
				)
			caller_member = conv.members_active.get(cli_session_id)
			if caller_member is None:
				return "ERROR: session bound to conversation but not a member."
			old_sender = caller_member.sender
			renamed = old_sender != sender
			actual_sender = sender
			if renamed:
				from server.conversation_ops import _disambiguate_sender
				actual_sender = _disambiguate_sender(conv, sender, exclude_session_id=cli_session_id)
				caller_member.sender = actual_sender
				if registry.sessions is not None:
					registry.sessions.set_sender(cli_session_id, actual_sender)
			registry.open_conversation_id = conv_id
			_spawn_bg(
				backend.set_open_conversation_id(conv_id),
				label=f"fb_set_open_conv_id:{conv_id}",
			)
			if renamed:
				_spawn_bg(
					backend.remove_conversation_member(conv_id, old_sender),
					label=f"fb_remove_member:{conv_id}:{old_sender}",
				)
				_spawn_bg(
					backend.write_conversation_member(conv_id, caller_member),
					label=f"fb_write_member:{conv_id}:{actual_sender}",
				)
		result = f"ok. open_conversation = {conv_id}"
		if actual_sender != sender:
			result += f", sender = {actual_sender} (your requested '{sender}' was already taken)"
		await logger.info(f"open_conversation: promoted conv_id={conv_id} sender={actual_sender}")
		return result

	@require_cli_session_id
	@_touch_sessions
	async def enter_conversation(
		sender: str,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		from server.conversation_ops import _add_member, _migrate_member, _queue_for_intro

		current_id = registry.session_to_conversation_id.get(cli_session_id)
		open_id = registry.open_conversation_id

		if current_id is not None:
			# Caller is bound to some conversation
			if open_id is None or open_id == current_id:
				# Branch 1 / Branch 5: queue in current. Ensure the (possibly
				# fresh-spawn-bound, member-less) caller is a member first, else
				# _queue_for_intro returns "caller not a member".
				from server.conversation_ops import _resolve_conversation_and_member
				await _resolve_conversation_and_member(registry, cli_session_id, cwd, sender, backend=backend)
				await logger.info(f"enter_conversation: queue_in_current conv_id={current_id} sender={sender}")
				return await _queue_for_intro(registry, current_id, cli_session_id, sender, cwd, config.timeout_seconds)
			# Branch 3: migrate from current to open
			conv_open = registry.conversations.get(open_id)
			conv_current = registry.conversations.get(current_id)
			if conv_open is None or conv_open.state != "active":
				return "ERROR: open conversation is not Active."
			# Lock both in id order to avoid AB-BA deadlock
			locks = sorted([conv_current, conv_open], key=lambda c: c.id)
			async with locks[0].lock, locks[1].lock:
				await _migrate_member(registry, current_id, open_id, cli_session_id, sender, cwd, backend=backend)
			await logger.info(f"enter_conversation: migrated {current_id}->{open_id} sender={sender}")
			return await _queue_for_intro(registry, open_id, cli_session_id, sender, cwd, config.timeout_seconds)

		# Caller unbound
		if open_id is None:
			# Branch 4: error
			return (
				"ERROR: no open conversation. Ask John to open one on the phone, or have "
				"an agent already in a conversation call open_conversation."
			)
		# Branch 2: join open
		conv_open = registry.conversations.get(open_id)
		if conv_open is None or conv_open.state != "active":
			return "ERROR: open conversation is not Active."
		async with conv_open.lock:
			await _add_member(registry, open_id, cli_session_id, sender, cwd, backend=backend)
		await logger.info(f"enter_conversation: joined_open conv_id={open_id} sender={sender}")
		return await _queue_for_intro(registry, open_id, cli_session_id, sender, cwd, config.timeout_seconds)

	@require_cli_session_id
	@_touch_sessions
	async def lookup_conversation_ids(
		cwd_filter: str | None = None,
		sender_contains: str | None = None,
		title_contains: str | None = None,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		"""Returns an ok envelope carrying the matching active conversation_ids.
		At least one of cwd_filter, sender_contains, title_contains must be supplied."""
		if not any([cwd_filter, sender_contains, title_contains]):
			return "ERROR: at least one of cwd_filter, sender_contains, title_contains is required"
		results = []
		for conv_id, conv in registry.conversations.items():
			if conv.state != "active":
				continue
			if title_contains and title_contains.lower() not in conv.title.lower():
				continue
			if sender_contains:
				if not any(sender_contains.lower() in m.sender.lower() for m in conv.members_active.values()):
					continue
			if cwd_filter:
				if not any(cwd_filter.lower() == m.cwd.lower() for m in conv.members_active.values()):
					continue
			results.append(conv_id)
		await logger.info(f"lookup_conversation_ids: matched={len(results)}")
		return _envelope("ok", conversation_ids=results)

	@require_cli_session_id
	@_touch_sessions
	async def leave_conversation(
		sender: str,
		parting_message: str,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		if not parting_message:
			return "ERROR: parting_message is required."
		from server.conversation_ops import _wake_one_from
		from server.session_fallback import apply_fallback
		import time as _time

		conv_id = registry.session_to_conversation_id.get(cli_session_id)
		if conv_id is None:
			return "ERROR: not in any conversation."
		conv = registry.conversations.get(conv_id)
		if conv is None:
			return "ERROR: bound conversation no longer exists."

		async with conv.lock:
			caller_member = conv.members_active.get(cli_session_id)
			if caller_member is None:
				return "ERROR: session bound to conversation but not a member."

			# Append parting message
			now_ts = _time.time()
			parting_msg = {
				"seq": len(conv.messages),
				"sender": caller_member.sender,
				"type": "parting",
				"text": parting_message,
				"timestamp": _now_iso(),
			}
			conv.messages.append(parting_msg)
			conv.last_activity_at = now_ts

			# Write parting to /conversations/<id>/messages
			_spawn_bg(
				backend.write_conversation_message(conv_id, caller_member.sender, "parting", parting_message, format="plain"),
				label=f"fb_write_parting_msg:{conv_id}:{caller_member.sender}",
			)

			# Remove from members_active; add to members_history
			caller_member.left_at = now_ts
			old_sender = caller_member.sender
			del conv.members_active[cli_session_id]
			conv.members_history.append(caller_member)
			# Persist members_history entry so it survives restart
			_spawn_bg(
				backend.write_conversation_member_history(conv_id, caller_member),
				label=f"fb_write_member_history:{conv_id}:{caller_member.sender}",
			)

			# Wake FIFO-oldest (so peer gets the parting)
			_wake_one_from(conv)

			# Check terminal state: no alive members AND no dormant members → end conv
			has_dormant = any(not m.alive for m in conv.members_active.values())
			has_alive = any(m.alive for m in conv.members_active.values())
			conv_ended = not has_alive and not has_dormant
			open_cleared = False
			if conv_ended:
				conv.state = "ended"
				conv.ended_at = now_ts
				if registry.open_conversation_id == conv_id:
					registry.open_conversation_id = None
					open_cleared = True

			# Firebase writes for new /conversations/<id>/... schema
			_spawn_bg(
				backend.remove_conversation_member(conv_id, old_sender),
				label=f"fb_remove_member:{conv_id}:{old_sender}",
			)
			_spawn_bg(
				backend.set_conversation_last_activity(conv_id, now_ts),
				label=f"fb_last_activity:{conv_id}",
			)
			if conv_ended:
				_spawn_bg(
					backend.set_conversation_state(conv_id, "ended"),
					label=f"fb_set_state:{conv_id}:ended",
				)
			if open_cleared:
				_spawn_bg(
					backend.set_open_conversation_id(None),
					label=f"fb_clear_open_id:{conv_id}",
				)

		# Apply session-fallback OUTSIDE the lock (it may touch other conversations)
		apply_fallback(registry, cli_session_id, backend=backend)
		await logger.info(f"leave_conversation: conv_id={conv_id} sender={sender}")
		return _envelope("ok", conversation_id=conv_id)

	@require_cli_session_id
	@_touch_sessions
	async def set_away_mode(
		value: bool,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		"""Flip the global away_mode flag. Persisted to Firebase under /global_settings/away_mode."""
		if not isinstance(value, bool):
			return "ERROR: value must be a boolean"
		# Flip the in-memory flag FIRST (synchronous, before any await). The
		# bulk-resolve below has await points; if the flag were still True across
		# them, a concurrently-arriving ask_human would pass the at-desk gate and
		# register a new pending the snapshot does not cover, stranding it until
		# the 24h timeout. Flipping first makes any such call take the at-desk
		# redirect instead. Firebase persistence still happens after.
		registry.global_away_mode = value
		resolved = 0
		if value is False:
			pendings = registry.all_pending()
			if pendings:
				before = registry.pending_count
				# Decided 2026-06-11 (P1-8): exiting away mode from the tool
				# side resolves every pending ask_human with the at-desk
				# notice, so blocked askers wake in their own terminals (the
				# canonical at-desk surface, consistent with R1) instead of
				# blocking until the 24h timeout. Same bulk entry point as the
				# phone's exit modal.
				from server.gateway.bulk_respond import _apply_bulk_respond_decision
				try:
					await _apply_bulk_respond_decision(
						registry, backend, logger,
						decision="send_default",
						default_text="John is back at his desk; your question was not answered remotely. Re-ask in the terminal.",
					)
					resolved = before - registry.pending_count
				except Exception as exc:
					await logger.surface_error(f"set_away_mode_bulk_resolve_failed: {exc}")
		try:
			if hasattr(backend, "set_global_away_mode"):
				await backend.set_global_away_mode(value)
		except Exception as exc:
			# Persist failed: the in-memory flag is set but the phone's
			# Firebase-read pill will not see it until restart (registry/phone
			# split-brain). Surface ERROR so the caller knows (F-67, decided
			# 2026-06-13).
			await logger.surface_error(f"set_away_mode_persist_failed: {exc}")
			return f"ERROR: away_mode set in memory but Firebase persist failed: {exc}"
		await logger.info(f"set_away_mode: value={value} resolved={resolved}")
		if value is False and resolved:
			return f"ok. away_mode=False ({resolved} pending question(s) resolved with the at-desk notice)"
		return f"ok. away_mode={value}"

	@require_cli_session_id
	@_touch_sessions
	async def combine_conversations(
		source_id: str,
		target_id: str,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		from server.conversation_ops import _perform_combine
		from pathlib import Path

		pending_dir = None
		if hasattr(config, "log_path") and config.log_path:
			pending_dir = Path(config.log_path).parent
		result = await _perform_combine(registry, source_id, target_id, logger, pending_dir, backend=backend)
		if result.startswith("ERROR"):
			return result
		await logger.info(f"combine_conversations: source={source_id} target={target_id}")
		return _envelope("ok", source=source_id, target=target_id, detail=result)

	@require_cli_session_id
	@_touch_sessions
	async def join_conversation(
		sender: str,
		ref: str | None = None,
		title: str | None = None,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		if err := _validate_sender(sender):
			return err
		from server.conversation_ops import (
			_add_member,
			_compose_wake_payload,
			_create_active_conversation_for,
			_migrate_member,
		)

		bound_id = registry.session_to_conversation_id.get(cli_session_id)

		if ref is not None:
			target = registry.conversations.get(ref)
			if target is None or target.state != "active":
				return f"ERROR: conversation {ref} not found or not Active."
			target_id = ref
		else:
			open_id = registry.open_conversation_id
			open_conv = registry.conversations.get(open_id) if open_id else None
			if open_conv is not None and open_conv.state == "active":
				target_id = open_conv.id
			else:
				# Mint and promote. The promotion is what pairs up the second
				# ref-absent joiner during the deprecation window: without it the
				# next joiner would mint a second, separate room.
				conv_id = await _create_active_conversation_for(
					registry, cli_session_id, cwd, sender, backend=backend, title=title,
				)
				registry.open_conversation_id = conv_id
				_spawn_bg(
					backend.set_open_conversation_id(conv_id),
					label=f"fb_set_open_conv_id:{conv_id}",
				)
				await logger.info(f"join_conversation: minted+promoted conv_id={conv_id} sender={sender}")
				return _envelope("ok", conversation_id=conv_id, minted=True, sender=sender, peers=[])

		conv = registry.conversations[target_id]
		already_member = False

		if bound_id == target_id:
			if cli_session_id in conv.members_active:
				already_member = True
			else:
				async with conv.lock:
					if cli_session_id not in conv.members_active:
						await _add_member(registry, target_id, cli_session_id, sender, cwd, backend=backend)
		elif bound_id is not None:
			source = registry.conversations.get(bound_id)
			if source is None:
				async with conv.lock:
					await _add_member(registry, target_id, cli_session_id, sender, cwd, backend=backend)
			else:
				locks = sorted([source, conv], key=lambda c: c.id)
				async with locks[0].lock, locks[1].lock:
					await _migrate_member(registry, bound_id, target_id, cli_session_id, sender, cwd, backend=backend)
		else:
			async with conv.lock:
				await _add_member(registry, target_id, cli_session_id, sender, cwd, backend=backend)

		member = conv.members_active.get(cli_session_id)
		if member is None:
			return "ERROR: join failed: session is not a member after join."
		# Unseen history is delivered synchronously - the reason no intro-queue
		# block exists on this path. Full history for a new member (last_seen 0),
		# delta for an existing one; cursor advances so the next wake is a delta.
		log = _compose_wake_payload(conv, member, "enter")
		member.last_seen_seq = len(conv.messages)
		peers = [m.sender for sid, m in conv.members_active.items() if sid != cli_session_id]
		await logger.info(f"join_conversation: conv_id={target_id} sender={member.sender} already_member={already_member}")
		return _envelope(
			"ok", conversation_id=target_id, sender=member.sender, peers=peers,
			log=log or None, already_member=already_member or None,
		)

	async def handle_agent_status(session_id: str, state: str, detail: str | None) -> None:
		"""Hook-driven write. Fire-and-forget — never raises to the caller.
		Gated on away-mode: writes are only made when John is in away mode.
		At-desk events are silently dropped — when John is at the terminal he's
		reading the live conversation, not the phone status row, so the Firebase
		write would be pure cost with no observer.

		session_id is the sole routing key: it resolves conv_id + sender. If the
		session is unbound or the conversation cannot be found, the write is
		dropped — agent status outside an active conversation has nowhere to go."""
		if not registry.global_away_mode:
			return
		# Truncate oversized detail rather than rejecting the write.
		if detail is not None and len(detail) > 200:
			detail = detail[:200]
		# Must have session_id to resolve conv_id and sender.
		if not session_id:
			return
		conv_id = registry.session_to_conversation_id.get(session_id)
		if not conv_id or conv_id not in registry.conversations:
			return
		conv = registry.conversations[conv_id]
		member = conv.members_active.get(session_id)
		if member is None:
			return
		sender = member.sender
		try:
			await backend.write_agent_status(conv_id, sender, state, detail)
		except Exception as exc:
			await logger.surface_error(f"agent_status_backend_error: {exc}")

	return ToolHandlers(
		ask_human=ask_human,
		notify_human=notify_human,
		send_document_human=send_document_human,
		message_and_await_agent=message_and_await_agent,
		open_conversation=open_conversation,
		enter_conversation=enter_conversation,
		lookup_conversation_ids=lookup_conversation_ids,
		leave_conversation=leave_conversation,
		set_away_mode=set_away_mode,
		combine_conversations=combine_conversations,
		join_conversation=join_conversation,
		handle_agent_status=handle_agent_status,
	)
