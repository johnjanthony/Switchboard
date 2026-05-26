"""Messenger backend trait surfaces and shared types.

Defines the abstract trait classes (Backend, MessageWriter, ResponsePoller,
AwayModeMirror, ChannelLifecycle, InjectPort) that backend implementations
must satisfy. The transport layer can evolve without touching the gateway
core, since gateway handlers depend on specific traits rather than a
god-interface.
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

	`correlation` is whatever opaque token the backend stored at
	`send_question` time (e.g. Telegram message_id). The gateway uses it
	to look up the pending request_id in the registry.

	`slot` is the literal storage key the response was read from (e.g. the
	Firebase RTDB child under `responses/`). Carried so the dispatcher can
	clean up unroutable / stale responses without having to reconstruct the
	key from correlation fields.
	"""

	correlation: CorrelationToken
	text: str
	slot: str | None = None


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

	async def send_spawn_ack(self, channel_id: str, prompt: str | None) -> None:
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
	"""Response/command queues, cleanup, startup reset."""

	@abstractmethod
	def poll_responses(self) -> "AsyncIterator[IncomingResponse]":
		"""Yield IncomingResponse as replies arrive. Infinite async iterator."""

	@abstractmethod
	def poll_commands(self) -> "AsyncIterator[str]":
		"""Yield slash-commands as they arrive. Infinite async iterator."""

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
		"""Zero out channels/*/pending_responses for every channel. Called once
		at server startup to match the post-restart in-memory Registry state. No-op default."""
		pass


class AwayModeMirror(ABC):
	"""Away-mode state mirror, listeners, startup resets."""

	async def write_away_mode_mirror(self, cwd: str | None, active: bool | None) -> None:
		"""Mirror away-mode state to Firebase.
		cwd=None for global flag changes; cwd=<canonical> for per-cwd overrides.
		active=None (only valid with cwd set) means the override entry should be
		removed from the mirror.
		No-op by default; FirebaseBackend overrides."""
		pass

	async def load_away_mode_snapshot(self, registry) -> None:
		"""Read current global + per-channel away-mode state from the backend
		and seed the registry's in-memory cache. Called once at server startup
		before the gateway accepts requests. No-op default."""
		pass

	async def start_away_mode_listeners(self, registry) -> None:
		"""Subscribe to backend value events for global and per-channel away-mode
		state; invoke registry.update_global_away_cache and
		registry.update_cwd_override_cache as changes arrive. No-op default."""
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


class ChannelLifecycle(ABC):
	"""Channel-state CRUD plus spawn-collision sub-flow."""

	async def read_channel_meta(self, cwd: str) -> dict:
		"""Return {'title': str|None, 'last_activity_at': str|None, 'hidden': bool}."""
		return {"title": None, "last_activity_at": None, "hidden": False}

	async def set_conversation_hidden(self, conv_id: str, hidden: bool) -> None:
		"""Set the hidden flag on the conversation at /conversations/<conv_id>/meta/hidden."""
		pass


class InjectPort(ABC):
	"""Per-session inject listener."""

	async def start_inject_listener(self, session_id: str) -> None:
		"""Start listening for human inject messages. No-op by default."""
		pass

	async def poll_inject_messages(self):
		"""Yield (session_id, inject_id, text) tuples. Empty by default."""
		if False:
			yield


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
	) -> None:
		"""Write the top-level conversation fields (everything except members and messages)."""
		pass

	async def write_conversation_member(self, conv_id: str, member) -> None:
		"""Write a member entry under /conversations/<id>/members_active/<sender>."""
		pass

	async def remove_conversation_member(self, conv_id: str, sender: str) -> None:
		"""Remove a member entry under /conversations/<id>/members_active/<sender>."""
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
		- Expanded form: write_conversation_message(conv_id, sender, type, text, ...) -> (correlation, msg_id)

		No-op by default; FirebaseBackend overrides with the full implementation."""
		if isinstance(sender_or_message, dict):
			return ""
		return (conv_id, sender_or_message), None

	async def set_conversation_state(self, conv_id: str, state: str) -> None:
		"""Update a conversation's state (active/ended)."""
		pass

	async def set_conversation_last_activity(self, conv_id: str, ts: float) -> None:
		"""Update /conversations/<id>/meta/last_activity_at."""
		pass

	async def set_open_conversation_id(self, conv_id: str | None) -> None:
		"""Write the global open-conversation pointer."""
		pass

	async def set_session_home(self, session_id: str, conv_id: str) -> None:
		"""Persist a cli session's home-conversation pointer."""
		pass

	async def remove_session_binding(self, session_id: str) -> None:
		"""Remove /cli_sessions/<session_id>/home_conversation_id."""
		pass

	async def set_global_wsl_available(self, available: bool) -> None:
		"""Write whether WSL is detected on this host to /global_settings/wsl_available."""
		pass
