"""Tests for the notify_human tool handler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse, MessengerBackend
from server.rate_limiter import RateLimiter
from server.registry import Registry


class RecordingBackend(MessengerBackend):
	def __init__(self) -> None:
		self.channel_messages: list[dict] = []
		self.sent_timeouts: list[tuple] = []
		self.sent_confirmations: list[tuple] = []
		self.session_metas: list[dict] = []
		self.inject_listeners: list[str] = []
		self._next_correlation = 1000

	async def write_channel_message(
		self, channel_id, sender, message_type, content,
		*, request_id=None, url=None, format="plain", suggestions=None, filename=None, title=None,
	):
		msg_id = f"msg_{len(self.channel_messages)}"
		data = {
			"channel_id": channel_id,
			"sender": sender,
			"message_type": message_type,
			"content": content,
			"request_id": request_id,
			"url": url,
			"format": format,
			"suggestions": suggestions,
			"filename": filename,
			"title": title,
			"msg_id": msg_id,
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

	async def write_response_text(self, channel_id, msg_id, text):
		for m in self.channel_messages:
			if m["channel_id"] == channel_id and m["msg_id"] == msg_id:
				m["response_text"] = text
				return

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

	async def write_session_meta(
		self, channel_id: str, session_type: str, project_key: str, **kwargs
	) -> None:
		self.session_metas.append({
			"channel_id": channel_id,
			"session_type": session_type,
			"project_key": project_key,
			**kwargs,
		})

	async def start_inject_listener(self, channel_id: str) -> None:
		self.inject_listeners.append(channel_id)

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


@pytest.mark.asyncio
async def test_notify_human_calls_backend_and_returns_ok(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("starting migration", "c:/work/test-001", "Claude")

	assert result == "ok"
	assert backend.sent_notifications == [("Claude", "starting migration")]
	# Session log path with canonical cwd is a known Slice J concern — not validated here.


class BrokenNotifyBackend(RecordingBackend):
	async def write_channel_message(self, channel_id, sender, message_type, content, **kwargs):
		if message_type == "notify":
			raise RuntimeError("notify boom")
		return await super().write_channel_message(channel_id, sender, message_type, content, **kwargs)


@pytest.mark.asyncio
async def test_notify_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenNotifyBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("starting", "c:/work/test-001", "Claude")

	assert result.startswith("ERROR:")
	assert "notify boom" in result


@pytest.mark.asyncio
async def test_notify_human_returns_error_when_rate_limited(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	limiter = RateLimiter(rate_per_minute=2)
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter)

	await handlers.notify_human("first", "c:/work/rl-001", "Claude")
	await handlers.notify_human("second", "c:/work/rl-001", "Claude")
	result = await handlers.notify_human("third", "c:/work/rl-001", "Claude")  # over limit

	assert result.startswith("ERROR: rate limit exceeded")
	assert "2 messages/min" in result
	assert "30 seconds" in result  # ceil(60/2) = 30
	assert len(backend.sent_notifications) == 2  # third call did not reach backend


@pytest.mark.asyncio
async def test_notify_human_no_limiter_is_unlimited(cfg, logger):
	"""Passing no limiter (default None) never rate-limits."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)  # no limiter

	for i in range(50):
		result = await handlers.notify_human(f"msg {i}", "c:/work/rl-002", "Claude")
		assert result == "ok"


@pytest.mark.asyncio
async def test_notify_human_rate_limit_is_per_channel(cfg, logger):
	"""Exhausting one channel does not affect a different channel."""
	backend = RecordingBackend()
	registry = Registry()
	limiter = RateLimiter(rate_per_minute=1)
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter)

	await handlers.notify_human("only msg", "c:/work/chan-a", "Claude")          # exhausts chan-a
	assert (await handlers.notify_human("extra", "c:/work/chan-a", "Claude")).startswith("ERROR:")  # chan-a limited
	result = await handlers.notify_human("hello", "c:/work/chan-b", "Claude")    # chan-b unaffected
	assert result == "ok"


@pytest.mark.asyncio
async def test_notify_human_title_passthrough(cfg, logger):
	"""title kwarg is forwarded to backend.write_channel_message."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("status update", "c:/work/sw", "Claude", title="My Session")

	assert result == "ok"
	assert len(backend.channel_messages) == 1
	assert backend.channel_messages[0]["title"] == "My Session"


@pytest.mark.asyncio
async def test_notify_human_invalid_cwd_returns_error(cfg, logger):
	"""Non-absolute cwd returns an error string without calling the backend."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("msg", "not-a-path", "Claude")

	assert result.startswith("ERROR: invalid cwd:")
	assert len(backend.channel_messages) == 0
