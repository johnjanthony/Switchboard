"""Messenger backend trait surfaces and shared types.

Defines the abstract trait classes (Backend, MessageWriter, ResponsePoller,
AwayModeMirror, ChannelLifecycle) that backend implementations must satisfy.
The transport layer can evolve without touching the gateway core, since
gateway handlers depend on specific traits rather than a god-interface.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

CorrelationToken = Any


@dataclass
class IncomingResponse:
	"""A response arriving from the messenger backend.

	`correlation` is the conversation_id string on every live path. The
	gateway uses it to look up the pending request_id in the registry.

	`slot` is the literal storage key the response was read from (e.g. the
	Firebase RTDB child under `conversations/<id>/answers/`). Carried so the
	dispatcher can clean up unroutable / stale responses without having to
	reconstruct the key from correlation fields.

	`request_id` is the exact request_id the answer was minted for. Carried
	so the dispatcher can pass it to registry.resolve as a guard, ensuring a
	replayed or stale answer does not resolve a newer entry at the same
	conversation_id (T-148).
	"""

	correlation: CorrelationToken
	text: str
	slot: str | None = None
	request_id: str | None = None
	"""The request_id the answer was written for. Carried so the dispatcher can
	resolve the EXACT pending entry it was minted for, rejecting a replayed or
	stale answer that lands after the entry was superseded (T-148)."""
	sender: str | None = None
	"""Who sent the reply, carried along for display/logging only. Resolution
	is keyed by (conversation_id, request_id); sender plays no routing role."""


class Backend(ABC):
	"""Lifecycle base — every backend must release resources on shutdown."""

	@abstractmethod
	async def aclose(self) -> None:
		"""Release any resources held by the backend."""


class MessageWriter(ABC):
	"""Conversation-message writes, acks, system messages.

	Anything that produces or annotates a message bubble in the conversation.
	write_conversation_message is the canonical write method (declared in
	ConversationStore). MessageWriter is retained as a mixin for backends
	that also implement send_timeout_followup, send_resolution_confirmation,
	and the various send_* helpers.
	"""

	@abstractmethod
	async def send_timeout_followup(
		self,
		request_id: str,
		conversation_id: str,
		timeout_seconds: int,
		correlation: "CorrelationToken",
	) -> None:
		"""Inform the developer a pending question has timed out."""

	@abstractmethod
	async def send_resolution_confirmation(
		self,
		request_id: str,
		conversation_id: str,
		correlation: "CorrelationToken",
		response_text: str | None = None,
	) -> None:
		"""Confirm to the developer that their response was received."""

	async def send_text(self, text: str) -> None:
		"""Send a simple text notification to the primary administrative channel."""
		pass

	async def send_spawn_ack(self, conversation_id: str, prompt: str | None) -> None:
		"""Acknowledge a successful spawn command. No-op by default."""
		pass

	async def send_stale_reply_notice(self, conversation_id: str, sender: str) -> None:
		"""Write a system message indicating a stale reply landed.
		No-op default; FirebaseBackend overrides."""
		pass

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		"""Mark the question with this request_id as cancelled in storage.
		No-op default; FirebaseBackend overrides."""
		pass

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
		"""Write a tracking record at /conversations/<id>/pending_questions/<request_id>.
		Used by phone-side UI to render an indicator that an ask_human is in flight.
		No-op default; FirebaseBackend overrides."""
		pass

	async def remove_pending_question_record(
		self,
		conversation_id: str,
		request_id: str,
	) -> None:
		"""Delete /conversations/<id>/pending_questions/<request_id> when the
		ask_human resolves (success / timeout / cancel). No-op default."""
		pass

	async def write_agent_status(
		self,
		conv_id: str,
		sender: str,
		state: str,
		detail: str | None,
	) -> None:
		"""Write the current agent status to /conversations/<conv_id>/agent_status/<sender>.
		Keyed by sender so multi-member conversations have per-member status entries.
		Passing state == "clear" deletes the entry. No-op default; FirebaseBackend overrides."""
		pass


class ResponsePoller(ABC):
	"""Response queue, cleanup, startup reset."""

	@abstractmethod
	def poll_responses(self) -> "AsyncIterator[IncomingResponse]":
		"""Yield IncomingResponse as replies arrive. Infinite async iterator."""

	async def poll_away_mode_commands(self) -> "AsyncIterator[dict]":
		"""Yield away_mode_commands queue entries as they arrive. No-op by default."""
		if False:
			yield

	async def delete_response_slot(self, slot: str) -> None:
		"""Delete a response entry under `responses/` by its literal storage key.
		Called by the dispatcher after a stale / unroutable response so the
		listener doesn't re-fire it forever. No-op default; FirebaseBackend overrides."""
		pass

	async def reset_all_pending_responses(self) -> None:
		"""Zero out conversations/*/pending_responses for every conversation. Called once
		at server startup to match the post-restart in-memory Registry state. No-op default."""
		pass


class AwayModeMirror(ABC):
	"""Away-mode state mirror, listeners, startup resets."""

	async def load_away_mode_snapshot(self, registry) -> None:
		"""Read current global + per-channel away-mode state from the backend
		and seed the registry's in-memory cache. Called once at server startup
		before the gateway accepts requests. No-op default."""
		pass

	async def start_away_mode_listeners(self, registry) -> None:
		"""Subscribe to the backend's global away-mode value event; invoke
		registry.update_global_away_cache as changes arrive. Per-channel
		overrides were retired in the conversations redesign — only the
		single global flag remains. No-op default."""
		pass

	async def reset_all_away_mode(self) -> None:
		"""Force away mode off globally and clear all per-channel overrides on
		startup. Decouples post-restart away-mode state from the now-broken MCP
		sessions of any pre-restart agents. No-op default; FirebaseBackend overrides."""
		pass

	async def delete_legacy_away_mode_node(self) -> None:
		"""Delete the legacy /away_mode top-level node (one-shot migration).
		No-op default."""
		pass

	async def delete_open_conversation_node(self) -> None:
		"""Delete the legacy /open_conversation top-level node (one-shot migration).
		No-op default."""
		pass


class ChannelLifecycle(ABC):
	"""Channel-state CRUD plus spawn-collision sub-flow."""

	async def set_conversation_hidden(self, conv_id: str, hidden: bool) -> None:
		"""Set the hidden flag on the conversation at /conversations/<conv_id>/meta/hidden."""
		pass


class ConversationStore:
	"""Backend protocol for persisting the new /conversations/<id>/... Firebase schema.

	All methods are no-ops by default so existing backends that don't implement them
	continue to work unchanged. FirebaseBackend overrides all methods.
	"""

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
		pass

	async def write_conversation_member(self, conv_id: str, member) -> None:
		"""Write a member entry under /conversations/<id>/members_active/<sender>."""
		pass

	async def remove_conversation_member(self, conv_id: str, sender: str) -> None:
		"""Remove a member entry under /conversations/<id>/members_active/<sender>."""
		pass

	async def write_conversation_member_history(self, conv_id: str, member) -> None:
		"""Write a departed member to /conversations/<id>/members_history/<sender>.
		Persists parting metadata (left_at, session_ended_at, session_end_reason) so
		hydration can restore members_history after restart."""
		pass

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
	):
		"""Append a message to /conversations/<id>/messages.

		Two call forms:
		- Legacy dict form: write_conversation_message(conv_id, message_dict) -> str push key
		- Expanded form: write_conversation_message(conv_id, sender, type, text, ...) -> (conv_id, msg_id)

		No-op by default; FirebaseBackend overrides with the full implementation."""
		if isinstance(sender_or_message, dict):
			return ""
		return conv_id, None

	async def set_conversation_state(self, conv_id: str, state: str) -> None:
		"""Update a conversation's state (active/ended)."""
		pass

	async def set_conversation_last_activity(self, conv_id: str, ts: float) -> None:
		"""Update /conversations/<id>/meta/last_activity_at."""
		pass

	async def write_conversation_title(self, conv_id: str, title: str) -> None:
		"""Update /conversations/<id>/meta/title (partial write). No-op default."""
		pass

	async def set_session_home(self, session_id: str, conv_id: str | None) -> None:
		"""Persist a cli session's home-conversation pointer.

		Pass conv_id=None to clear (delete) the stored home pointer for this session
		— used by session-fallback when a dormant session's home conv has Ended
		and we want to avoid leaving a stale pointer behind in storage."""
		pass

	async def set_global_wsl_available(self, available: bool) -> None:
		"""Write whether WSL is detected on this host to /global_settings/wsl_available."""
		pass
