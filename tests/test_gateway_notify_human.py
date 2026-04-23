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
from server.registry import Registry


class RecordingBackend(MessengerBackend):
	def __init__(self) -> None:
		self.channel_messages: list[dict] = []
		self.sent_timeouts: list[tuple] = []
		self.sent_confirmations: list[tuple] = []
		self._next_correlation = 1000

	async def write_channel_message(
		self, channel_id, sender, message_type, content,
		*, request_id=None, url=None, format="plain", suggestions=None, filename=None,
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

	result = await handlers.notify_human("starting migration", "chan-test-001")

	assert result == "ok"
	assert backend.sent_notifications == [("Claude", "starting migration")]

	sessions_dir = tmp_path / "sessions"
	session_files = list(sessions_dir.glob("chan-test-001_*.log"))
	assert len(session_files) == 1
	assert "starting migration" in session_files[0].read_text()


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

	result = await handlers.notify_human("starting", "chan-test-001")

	assert result.startswith("ERROR:")
	assert "notify boom" in result
