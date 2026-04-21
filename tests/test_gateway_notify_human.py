"""Tests for the notify_human tool handler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse, MessengerBackend
from server.registry import Registry


class RecordingBackend(MessengerBackend):
	def __init__(self) -> None:
		self.sent_questions: list[tuple[str, str, str]] = []
		self.sent_notifications: list[tuple[str, str]] = []
		self.sent_timeouts: list[tuple[str, str, int, Any]] = []
		self.sent_confirmations: list[tuple[str, str, Any]] = []
		self.sent_documents: list[tuple[str, str, Any]] = []
		self._next_correlation = 1000

	async def send_question(self, request_id, agent_id, question, format="plain", suggestions=None):
		correlation = self._next_correlation
		self._next_correlation += 1
		self.sent_questions.append((request_id, agent_id, question))
		return correlation

	async def send_notification(self, agent_id, message, format="plain"):
		self.sent_notifications.append((agent_id, message))

	async def send_timeout_followup(
		self, request_id, agent_id, timeout_seconds, correlation
	):
		self.sent_timeouts.append(
			(request_id, agent_id, timeout_seconds, correlation)
		)

	async def send_resolution_confirmation(
		self, request_id, agent_id, correlation
	):
		self.sent_confirmations.append((request_id, agent_id, correlation))

	async def send_document(self, agent_id, path, caption=None):
		self.sent_documents.append((agent_id, str(path), caption))

	async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
		if False:
			yield  # pragma: no cover
		return


@pytest.fixture
def cfg(tmp_path):
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg, tmp_path):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_notify_human_calls_backend_and_returns_ok(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("starting migration", "IR2")

	assert result == "ok"
	assert backend.sent_notifications == [("IR2", "starting migration")]


class BrokenNotifyBackend(RecordingBackend):
	async def send_notification(self, agent_id, message, format="plain"):
		raise RuntimeError("notify boom")


@pytest.mark.asyncio
async def test_notify_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenNotifyBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.notify_human("starting", "IR2")

	assert result.startswith("ERROR:")
	assert "notify boom" in result
