"""P1-6 (M10): ask_human must consume the per-conversation rate limiter. The
at-desk branch is the spam vector: it writes a notify + FCM and returns
instantly, so a tight loop could page the phone without bound."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.rate_limiter import RateLimiter
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend


@pytest.mark.asyncio
async def test_at_desk_ask_human_loop_is_throttled(tmp_path: Path):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)
	registry = Registry()  # away OFF: at-desk branch
	conv = make_active_conversation(conversation_id="conv-r1", member_session_id="s-r1", sender="Claude")
	registry.conversations["conv-r1"] = conv
	registry.bind_session("s-r1", "conv-r1")
	backend = RecordingBackend()
	handlers = build_tool_handlers(
		cfg, registry, backend, JsonlLogger(cfg.log_path), limiter=RateLimiter(2),
	)

	r1 = await handlers.ask_human("q1?", "Claude", cli_session_id="s-r1", cwd="C:/Work/X")
	r2 = await handlers.ask_human("q2?", "Claude", cli_session_id="s-r1", cwd="C:/Work/X")
	r3 = await handlers.ask_human("q3?", "Claude", cli_session_id="s-r1", cwd="C:/Work/X")

	assert r1.startswith("ERROR: John is at his desk")
	assert r2.startswith("ERROR: John is at his desk")
	assert r3.startswith("ERROR: rate limit exceeded"), f"third call must be throttled, got: {r3!r}"
	assert len(backend.channel_messages) == 2, "the throttled call must not write a notification"
