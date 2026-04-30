"""Timeout and error-path tests for ask_human."""

import pytest

from server.config import Config
from server.gateway import TIMEOUT_SENTINEL, build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_registry_with_loopback
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
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("Overwrite foo?", _CWD, _SENDER)

	assert result == TIMEOUT_SENTINEL
	# Backend was asked to send a timeout follow-up.
	assert len(backend.sent_timeouts) == 1
	assert backend.sent_timeouts[0][0] == backend.sent_questions[0][0]  # request_id matches
	# Registry entry is cleaned up — (cwd, sender) returns None.
	assert registry.resolve(cwd=_CWD, sender=_SENDER, text="late") is None


class BrokenBackend(RecordingBackend):
	async def write_channel_message(self, channel_id, sender, message_type, content, **kwargs):
		if message_type == "question":
			raise RuntimeError("boom")
		return await super().write_channel_message(channel_id, sender, message_type, content, **kwargs)


@pytest.mark.asyncio
async def test_ask_human_returns_error_sentinel_on_backend_failure(cfg, logger):
	backend = BrokenBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("q", _CWD, _SENDER)

	assert result.startswith("ERROR:")
	assert "boom" in result
