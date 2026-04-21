"""Timeout and error-path tests for ask_human."""

import pytest

from server.config import Config
from server.gateway import TIMEOUT_SENTINEL, build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


@pytest.fixture
def cfg(tmp_path):
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=0,  # immediate timeout for the test
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_ask_human_returns_sentinel_on_timeout(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("Overwrite foo?", "IR2")

	assert result == TIMEOUT_SENTINEL
	# Backend was asked to send a timeout follow-up.
	assert len(backend.sent_timeouts) == 1
	assert backend.sent_timeouts[0][0] == backend.sent_questions[0][0]
	# Registry entry is cleaned up.
	assert registry.resolve_by_correlation(1000, "late") is None


class BrokenBackend(RecordingBackend):
	async def send_question(self, request_id, agent_id, question, format="plain"):
		raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_ask_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("q", "IR2")

	assert result.startswith("ERROR:")
	assert "boom" in result
