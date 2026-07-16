"""Sender names interpolate into RTDB paths (members_active/<sender>,
agent_status/<sender>) and key pending attribution. Firebase-illegal key
characters must be rejected at the tool boundary and, as an invariant
guard, at the member-creation sites."""

from __future__ import annotations

import pytest

from server.conversation_ops import illegal_sender_reason


@pytest.mark.parametrize("sender", [
	"Claude Win", "Claude Win 2", "Reviewer", "smoke-1a2b3c4d", "Implementer (opus)",
])
def test_legal_senders_pass(sender):
	assert illegal_sender_reason(sender) is None


@pytest.mark.parametrize("sender,fragment", [
	("repo/reviewer", "/"),
	("Claude (v2.1)", "."),
	("a#b", "#"),
	("a$b", "$"),
	("a[b", "["),
	("a]b", "]"),
	("", "empty"),
	("   ", "empty"),
	("tab\tname", "control"),
	("nl\nname", "control"),
])
def test_illegal_senders_rejected(sender, fragment):
	reason = illegal_sender_reason(sender)
	assert reason is not None
	assert fragment in reason


@pytest.mark.asyncio
async def test_tool_boundary_rejects_illegal_sender(tmp_path):
	from server.config import Config
	from server.gateway import build_tool_handlers
	from server.logging_jsonl import JsonlLogger
	from server.registry import Registry
	from tests.test_gateway_notify_human import RecordingBackend

	cfg = Config(host="127.0.0.1", port=9876, timeout_seconds=5.0, log_path=str(tmp_path / "s.log"))
	handlers = build_tool_handlers(cfg, Registry(), RecordingBackend(), JsonlLogger(cfg.log_path))
	result = await handlers.notify_human("hello", "repo/reviewer", cli_session_id="s-1", cwd="C:/Work/X")
	assert result.startswith("ERROR: sender name")
	assert "/" in result
	# The double-underscore rule is unchanged.
	result2 = await handlers.notify_human("hello", "bad__name", cli_session_id="s-1", cwd="C:/Work/X")
	assert result2.startswith("ERROR: sender name")


@pytest.mark.asyncio
async def test_add_member_raises_on_illegal_sender(tmp_path):
	from server.conversation_ops import _add_member
	from server.registry import Conversation, Registry

	registry = Registry()
	registry.conversations["conv-1"] = Conversation(id="conv-1", title="T")
	with pytest.raises(ValueError):
		await _add_member(registry, "conv-1", "s-1", "repo/reviewer", "C:/Work/X")
	assert "s-1" not in registry.conversations["conv-1"].members_active
