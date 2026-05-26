"""Timeout and error-path tests for ask_human."""

import pytest

from server.config import Config
from server.gateway import TIMEOUT_SENTINEL, build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend

_CWD = "c:/work/sw"
_SENDER = "Claude"


@pytest.fixture
def cfg(tmp_path):
	return Config(
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

	result = await handlers.ask_human(
		"Overwrite foo?",
		_SENDER,
		cli_session_id="s-timeout-001",
		cwd=_CWD,
	)

	assert result == TIMEOUT_SENTINEL
	# Backend was asked to send a timeout follow-up.
	assert len(backend.sent_timeouts) == 1
	assert backend.sent_timeouts[0][0] == backend.sent_questions[0][0]  # request_id matches
	# Registry entry is cleaned up — no pending request remains.
	assert registry.pending_count == 0


class BrokenBackend(RecordingBackend):
	async def write_conversation_message(self, conv_id, sender_or_message, message_type=None, text=None, **kwargs):
		mt = message_type if not isinstance(sender_or_message, dict) else sender_or_message.get("type", "")
		if mt == "question":
			raise RuntimeError("boom")
		return await super().write_conversation_message(conv_id, sender_or_message, message_type, text, **kwargs)


@pytest.mark.asyncio
async def test_ask_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human(
		"q",
		_SENDER,
		cli_session_id="s-broken-001",
		cwd=_CWD,
	)

	assert result.startswith("ERROR:")
	assert "boom" in result
