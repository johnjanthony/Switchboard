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

_SESSION_START = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()

def _new_request_id() -> str:
	return uuid.uuid4().hex[:8]

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
	handle_agent_status: Callable[..., Coroutine[None, None, None]]

class _ToolHandlersBackend(MessageWriter, ChannelLifecycle, ConversationStore):
	"""Backend surface used by build_tool_handlers (and its closures)."""

def build_tool_handlers(
	config: Config,
	registry: Registry,
	backend: _ToolHandlersBackend,
	logger: JsonlLogger,
	limiter: RateLimiter | None = None,
) -> ToolHandlers:
	def _validate_sender(sender: str) -> str | None:
		if "__" in sender:
			return f"ERROR: sender name '{sender}' contains restricted characters '__'."
		return None

	def _rate_limit_error() -> str:
		return (
			f"ERROR: rate limit exceeded — you are sending too fast.\n"
			f"Limit is {limiter.rate_per_minute} messages/min per channel.\n"
			f"Wait at least {limiter.wait_seconds} seconds before retrying, or slow your notify cadence."
		)

	@require_cli_session_id
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
		from server.conversation_ops import _resolve_conversation_and_member
		conversation_id = await _resolve_conversation_and_member(
			registry, cli_session_id, cwd, sender, backend=backend,
		)

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
				conversation_id=conversation_id, sender=sender, request_id=request_id, msg_id=msg_id, return_superseded=True,
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
				registry.remove(conversation_id, sender)
				await _safe_mark_cancelled(backend, conversation_id, request_id, logger)
			raise
		except Exception as exc:
			await logger.tool_error(request_id, conversation_id, str(exc))
			return f"ERROR: {exc}"

		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
		except asyncio.TimeoutError:
			await logger.timeout(request_id, conversation_id, config.timeout_seconds)
			registry.remove(conversation_id, sender)
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
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			# See note above re: shielding. This is the live cancel-from-MCP
			# path that the user observes when pressing Esc on the tool call.
			with anyio.CancelScope(shield=True):
				registry.remove(conversation_id, sender)
				await _safe_mark_cancelled(backend, conversation_id, request_id, logger)
			raise
		except Exception as exc:
			await logger.tool_error(request_id, conversation_id, str(exc))
			registry.remove(conversation_id, sender)
			_spawn_bg(
				backend.mark_question_cancelled(conversation_id, request_id),
				label=f"fb_mark_cancelled:error:{conversation_id}:{request_id}",
			)
			return f"ERROR: {exc}"

		# Successful resolution: pending_questions record is cleared and
		# answered_question_msg_ids is marked. Both writes are best-effort.
		_spawn_bg(
			backend.remove_pending_question_record(conversation_id, request_id),
			label=f"fb_remove_pending_question:resolved:{conversation_id}:{request_id}",
		)
		if msg_id:
			_spawn_bg(
				backend.mark_question_answered(conversation_id, msg_id),
				label=f"fb_mark_question_answered:{conversation_id}:{msg_id}",
			)

		await _append_session_log(config.log_path, conversation_id, "←", result, logger)
		duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
		source = "unknown"
		if isinstance(correlation, dict):
			source = "multi"
		elif str(correlation).startswith("firebase_"):
			source = "firebase"
		elif str(correlation).startswith("android_"):
			source = "android_rest"
		await logger.request_resolved(request_id, conversation_id, response_text=result, source=source, duration_ms=duration_ms)
		try:
			await backend.send_resolution_confirmation(request_id, conversation_id, correlation, response_text=result)
		except Exception as exc:
			await logger.surface_error(f"resolution_confirmation_failed: {exc}", correlation=str(correlation))
		return result

	@require_cli_session_id
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
			return "ERROR: message is required. The 'listen without speaking' use case is enter_conversation()."

		from server.conversation_ops import _resolve_conversation_and_member
		conversation_id = await _resolve_conversation_and_member(
			registry, cli_session_id, cwd, sender, backend=backend, mint_if_unbound=False,
		)
		if conversation_id is None:
			return "ERROR: not in any conversation. End your turn."
		conv = registry.conversations.get(conversation_id)
		if conv is None:
			return "ERROR: bound conversation no longer exists."

		# Locate caller's member by cli_session_id
		caller_member = None
		for m in conv.members_active.values():
			if m.cli_session_id == cli_session_id:
				caller_member = m
				break
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
					old_key = caller_member.sender
					del conv.members_active[old_key]
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
						backend.remove_conversation_member(conversation_id, old_key),
						label=f"fb_remove_member:{conversation_id}:{old_key}",
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
			return empty_result

		# Sole-alive in the open-marker conv: hold the lobby until a peer joins
		# or we time out. end_conv_on_timeout=False so the established conv
		# survives a quiet round — caller can poll again or leave_conversation.
		if lobby_wait:
			from server.conversation_ops import _queue_for_open_peer
			return await _queue_for_open_peer(
				registry, conversation_id, cli_session_id,
				config.timeout_seconds, backend=backend,
				end_conv_on_timeout=False,
			)

		# Lock released; now wait
		try:
			result = await asyncio.wait_for(future, timeout=config.timeout_seconds)
			return result
		except asyncio.TimeoutError:
			async with conv.lock:
				if wait_entry in conv.wait_queue:
					conv.wait_queue.remove(wait_entry)
			return TIMEOUT_SENTINEL
		except asyncio.CancelledError:
			async with conv.lock:
				if wait_entry in conv.wait_queue:
					conv.wait_queue.remove(wait_entry)
			raise

	@require_cli_session_id
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
			return await _queue_for_open_peer(
				registry, conv_id, cli_session_id, config.timeout_seconds, backend=backend,
			)
		conv = registry.conversations.get(conv_id)
		if conv is None:
			return "ERROR: bound conversation no longer exists."
		# Ensure the (possibly fresh-spawn-bound, member-less) caller is a member
		# before the rename/open logic, which otherwise silently finds no member.
		from server.conversation_ops import _resolve_conversation_and_member
		await _resolve_conversation_and_member(registry, cli_session_id, cwd, sender, backend=backend)
		async with conv.lock:
			if title:
				conv.title = title
			# Locate and update the caller's member entry (rename if sender changed)
			caller_member = None
			old_key = None
			for s, m in conv.members_active.items():
				if m.cli_session_id == cli_session_id:
					caller_member = m
					old_key = s
					break
			renamed = caller_member is not None and old_key != sender
			actual_sender = sender
			if renamed:
				from server.conversation_ops import _disambiguate_sender
				# Check for collision (old_key is no longer in the dict once we delete it)
				del conv.members_active[old_key]
				actual_sender = _disambiguate_sender(conv, sender)
				caller_member.sender = actual_sender
				conv.members_active[actual_sender] = caller_member
			registry.open_conversation_id = conv_id
			_spawn_bg(
				backend.set_open_conversation_id(conv_id),
				label=f"fb_set_open_conv_id:{conv_id}",
			)
			if renamed:
				_spawn_bg(
					backend.remove_conversation_member(conv_id, old_key),
					label=f"fb_remove_member:{conv_id}:{old_key}",
				)
				_spawn_bg(
					backend.write_conversation_member(conv_id, caller_member),
					label=f"fb_write_member:{conv_id}:{actual_sender}",
				)
		result = f"ok. open_conversation = {conv_id}"
		if actual_sender != sender:
			result += f", sender = {actual_sender} (your requested '{sender}' was already taken)"
		return result

	@require_cli_session_id
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
		return await _queue_for_intro(registry, open_id, cli_session_id, sender, cwd, config.timeout_seconds)

	@require_cli_session_id
	async def lookup_conversation_ids(
		cwd_filter: str | None = None,
		sender_contains: str | None = None,
		title_contains: str | None = None,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		"""Returns a JSON-encoded list of matching active conversation_ids.
		At least one of cwd_filter, sender_contains, title_contains must be supplied."""
		import json
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
		return json.dumps(results)

	@require_cli_session_id
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
			# Find caller
			caller_member = None
			old_key = None
			for s, m in conv.members_active.items():
				if m.cli_session_id == cli_session_id:
					caller_member = m
					old_key = s
					break
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
			del conv.members_active[old_key]
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
				backend.remove_conversation_member(conv_id, old_key),
				label=f"fb_remove_member:{conv_id}:{old_key}",
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
		return f"ok. Left conversation {conv_id}."

	@require_cli_session_id
	async def set_away_mode(
		value: bool,
		*,
		cli_session_id: str,
		cwd: str,
	) -> str:
		"""Flip the global away_mode flag. Persisted to Firebase under /global_settings/away_mode."""
		if not isinstance(value, bool):
			return "ERROR: value must be a boolean"
		resolved = 0
		if value is False:
			pendings = registry.all_pending()
			if pendings:
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
					resolved = len(pendings)
				except Exception as exc:
					await logger.surface_error(f"set_away_mode_bulk_resolve_failed: {exc}")
		registry.global_away_mode = value
		try:
			if hasattr(backend, "set_global_away_mode"):
				await backend.set_global_away_mode(value)
			elif hasattr(backend, "set_away_mode"):
				await backend.set_away_mode(value)
		except Exception as exc:
			await logger.surface_error(f"set_away_mode_persist_failed: {exc}")
		if value is False and resolved:
			return f"ok. away_mode=False ({resolved} pending question(s) resolved with the at-desk notice)"
		return f"ok. away_mode={value}"

	@require_cli_session_id
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
		return await _perform_combine(registry, source_id, target_id, logger, pending_dir, backend=backend)

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
		sender = None
		for member in conv.members_active.values():
			if member.cli_session_id == session_id:
				sender = member.sender
				break
		if sender is None:
			return
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
		handle_agent_status=handle_agent_status,
	)
