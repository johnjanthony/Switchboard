"""Tests for the notify_human tool handler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.messenger import (
	IncomingResponse,
	Backend,
	MessageWriter,
	ResponsePoller,
	AwayModeMirror,
	ChannelLifecycle,
	InjectPort,
	ConversationStore,
)
from server.rate_limiter import RateLimiter
from server.registry import Registry


class RecordingBackend(MessageWriter, ResponsePoller, AwayModeMirror, ChannelLifecycle, InjectPort, ConversationStore, Backend):
	def __init__(self) -> None:
		self.channel_messages: list[dict] = []
		self.sent_timeouts: list[tuple] = []
		self.sent_confirmations: list[tuple] = []
		self.agent_status_writes: list[tuple] = []
		self.inject_listeners: list[str] = []
		self._next_correlation = 1000

	async def write_conversation_message(
		self,
		conv_id,
		sender_or_message,
		message_type=None,
		text=None,
		*,
		request_id=None,
		url=None,
		format="plain",
		suggestions=None,
		filename=None,
		title=None,
		rejected=False,
		attached_to_msg_id=None,
	):
		"""Record conversation-message writes. Handles both the legacy dict form
		and the expanded positional form so all migrated callers are captured."""
		if isinstance(sender_or_message, dict):
			# Legacy dict form: write_conversation_message(conv_id, message_dict)
			d = sender_or_message
			msg_id = f"msg_{len(self.channel_messages)}"
			data = {
				"channel_id": conv_id,
				"sender": d.get("sender", ""),
				"message_type": d.get("type", ""),
				"content": d.get("text", ""),
				"request_id": d.get("request_id"),
				"url": None,
				"format": d.get("format", "plain"),
				"suggestions": None,
				"filename": None,
				"title": d.get("title"),
				"msg_id": msg_id,
				"rejected": False,
				"attached_to_msg_id": None,
			}
			self.channel_messages.append(data)
			return msg_id

		# Expanded positional form: write_conversation_message(conv_id, sender, type, text, ...)
		sender = sender_or_message
		msg_id = f"msg_{len(self.channel_messages)}"
		data = {
			"channel_id": conv_id,
			"sender": sender,
			"message_type": message_type,
			"content": text,
			"request_id": request_id,
			"url": url,
			"format": format,
			"suggestions": suggestions,
			"filename": filename,
			"title": title,
			"msg_id": msg_id,
			"rejected": rejected,
			"attached_to_msg_id": attached_to_msg_id,
		}
		self.channel_messages.append(data)
		if message_type == "question":
			correlation = self._next_correlation
			self._next_correlation += 1
			return correlation, msg_id
		return None, msg_id

	async def send_timeout_followup(self, request_id, channel_id, timeout_seconds, correlation):
		self.sent_timeouts.append((request_id, channel_id, timeout_seconds, correlation))

	async def send_resolution_confirmation(self, request_id, channel_id, correlation, response_text=None):
		self.sent_confirmations.append((request_id, channel_id, correlation, response_text))

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		if False:
			yield
		return

	async def poll_commands(self) -> AsyncIterator[str]:
		if False:
			yield
		return

	async def aclose(self) -> None:
		pass

	async def start_inject_listener(self, channel_id: str) -> None:
		self.inject_listeners.append(channel_id)

	async def write_agent_status(self, conv_id, sender, state, detail):
		self.agent_status_writes.append((conv_id, sender, state, detail))

	# Helpers for assertions in existing tests
	@property
	def sent_questions(self):
		return [(m["request_id"], m["channel_id"], m["content"]) for m in self.channel_messages if m["message_type"] == "question"]

	@property
	def sent_notifications(self):
		return [(m["sender"], m["content"]) for m in self.channel_messages if m["message_type"] == "notify"]

	@property
	def sent_documents(self):
		# (channel_id, content/caption, url) for document messages
		return [(m["channel_id"], m["content"], m["url"]) for m in self.channel_messages if m["message_type"] == "document"]


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg, tmp_path):
	return JsonlLogger(cfg.log_path)


class BrokenNotifyBackend(RecordingBackend):
	async def write_conversation_message(self, conv_id, sender_or_message, message_type=None, text=None, **kwargs):
		mt = message_type if not isinstance(sender_or_message, dict) else sender_or_message.get("type", "")
		if mt == "notify":
			raise RuntimeError("notify boom")
		return await super().write_conversation_message(conv_id, sender_or_message, message_type, text, **kwargs)


@pytest.mark.asyncio
async def test_notify_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenNotifyBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human(
		"starting",
		"Claude",
		cli_session_id="s-broken-001",
		cwd="c:/work/test-001",
	)

	assert result.startswith("ERROR:")
	assert "notify boom" in result


@pytest.mark.asyncio
async def test_notify_human_returns_error_when_rate_limited(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	limiter = RateLimiter(rate_per_minute=2)
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter)

	# All three calls share the same session → same conversation.
	await handlers.notify_human("first", "Claude", cli_session_id="s-rl-001", cwd="c:/work/rl-001")
	await handlers.notify_human("second", "Claude", cli_session_id="s-rl-001", cwd="c:/work/rl-001")
	result = await handlers.notify_human("third", "Claude", cli_session_id="s-rl-001", cwd="c:/work/rl-001")  # over limit

	assert result.startswith("ERROR: rate limit exceeded")
	assert "2 messages/min" in result
	assert "30 seconds" in result  # ceil(60/2) = 30
	assert len(backend.sent_notifications) == 2  # third call did not reach backend



@pytest.mark.asyncio
async def test_notify_human_rate_limit_is_per_channel(cfg, logger):
	"""Exhausting one conversation does not affect a different conversation."""
	backend = RecordingBackend()
	registry = Registry()
	limiter = RateLimiter(rate_per_minute=1)
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter)

	# Two different sessions → two different conversations.
	await handlers.notify_human("only msg", "Claude", cli_session_id="s-chan-a", cwd="c:/work/chan-a")
	assert (await handlers.notify_human("extra", "Claude", cli_session_id="s-chan-a", cwd="c:/work/chan-a")).startswith("ERROR:")
	result = await handlers.notify_human("hello", "Claude", cli_session_id="s-chan-b", cwd="c:/work/chan-b")
	assert result == "ok"


@pytest.mark.asyncio
async def test_notify_human_title_passthrough(cfg, logger):
	"""title kwarg is forwarded to backend.write_conversation_message."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human(
		"status update",
		"Claude",
		title="My Session",
		cli_session_id="s-title-001",
		cwd="c:/work/sw",
	)

	assert result == "ok"
	assert len(backend.channel_messages) == 1
	assert backend.channel_messages[0]["title"] == "My Session"



@pytest.mark.asyncio
async def test_recording_backend_records_agent_status_writes():
	backend = RecordingBackend()
	await backend.write_agent_status("conv-abc", "Claude", "thinking", None)
	await backend.write_agent_status("conv-abc", "Claude", "tool:Bash", "npm test")
	assert backend.agent_status_writes == [
		("conv-abc", "Claude", "thinking", None),
		("conv-abc", "Claude", "tool:Bash", "npm test"),
	]
