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
	"""Channel-message writes, acks, system messages.

	Anything that produces or annotates a message bubble in the channel.
	"""

	@abstractmethod
	async def write_channel_message(
		self,
		cwd: str,
		sender: str,
		message_type: str,
		content: str,
		*,
		request_id: str | None = None,
		url: str | None = None,
		format: str = "plain",
		suggestions: list[str] | None = None,
		filename: str | None = None,
		title: str | None = None,
		rejected: bool = False,
		attached_to_msg_id: str | None = None,
	) -> "tuple[CorrelationToken | None, str | None]":
		"""Write a message to the channel. Returns (correlation, msg_id).
		correlation is used for message_type='question' to match responses.
		msg_id is the unique ID of the message in the backend.
		rejected=True marks system messages that the client should surface
		as a transient toast (e.g. stale-reply notices)."""

	@abstractmethod
	async def send_timeout_followup(
		self,
		request_id: str,
		channel_id: str,
		timeout_seconds: int,
		correlation: "CorrelationToken",
	) -> None:
		"""Inform the developer a pending question has timed out."""

	@abstractmethod
	async def send_resolution_confirmation(
		self,
		request_id: str,
		channel_id: str,
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

	async def send_stale_reply_notice(self, cwd: str, sender: str) -> None:
		"""Write a system message indicating a stale reply landed.
		No-op default; FirebaseBackend overrides."""
		pass

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		"""Mark the question with this request_id as cancelled in storage.
		No-op default; FirebaseBackend overrides."""
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

	async def write_session_meta(
		self,
		channel_id: str,
		type: str,
		project_key: str,
		*,
		agent_senders: list[str] | None = None,
		task: str | None = None,
	) -> None:
		"""Write session metadata to Firebase on session creation. No-op by default."""
		pass

	async def read_channel_meta(self, cwd: str) -> dict:
		"""Return {'title': str|None, 'last_activity_at': str|None, 'hidden': bool}."""
		return {"title": None, "last_activity_at": None, "hidden": False}

	async def has_messages(self, cwd: str) -> bool:
		"""Return True if channels/<key>/messages contains any entries."""
		return False

	async def wipe_channel(self, cwd: str) -> None:
		"""Atomic wipe of channels/<key>/messages, responses/<key>__*, etc."""
		pass

	async def set_channel_hidden(self, cwd: str, hidden: bool) -> None:
		"""Set the hidden flag on the channel."""
		pass

	async def write_spawn_collision_prompt(
		self, spawn_id: str, cwd: str,
		channel_title: str | None, last_activity_at: str | None, hidden: bool,
	) -> None:
		"""Push a spawn-collision dialog to the phone via Firebase."""
		pass

	async def clear_spawn_collision_prompt(self, spawn_id: str) -> None:
		"""Remove the spawn-collision dialog node."""
		pass

	async def poll_spawn_collision_decision(self, spawn_id: str) -> dict:
		"""Block until spawn_collisions/{spawn_id}/decision is written; return the decision dict.
		Decision shape: {"action": "continue" | "clear" | "cancel"}.
		Used by _handle_spawn to gate the collision-dialog flow."""
		raise NotImplementedError


class InjectPort(ABC):
	"""Per-session inject listener."""

	async def start_inject_listener(self, session_id: str) -> None:
		"""Start listening for human inject messages. No-op by default."""
		pass

	async def poll_inject_messages(self):
		"""Yield (session_id, inject_id, text) tuples. Empty by default."""
		if False:
			yield
