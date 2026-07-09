"""T-145: force-end must resolve a blocked ask_human future with a semantic
do-not-retry sentinel rather than cancelling it.

A cancelled future surfaces on the agent's MCP client as a transport-level
failure, which the agent retries; the retry either re-strands the agent on the
home conversation or mints orphan '(home)' state. Resolving the future with the
'__CONVERSATION_ENDED__\n(force-ended)' sentinel (the same string handle_force_end
already uses for wait_queue futures) gives the agent a terminal value it returns
normally and stops on.

Decisions (John, 2026-06-15): reuse the existing __CONVERSATION_ENDED__ sentinel;
add a dedicated resolve_pending_for_conversation method (leave
cancel_pending_for_conversation a true cancel for spawn's dead-agent cleanup);
and add a defensive guard so ask_human from a session bound to an Ended
conversation returns the sentinel instead of minting orphan state.

Superseded (WP-1 Task 7): resolve_pending_for_conversation and
cancel_pending_for_conversation were deleted once terminate_pending
(server/gateway/pending_lifecycle.py) became the single terminal-path owner;
handle_force_end now loops registry.pending_for_conversation and calls
terminate_pending per record with the same resolve-not-cancel semantics."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.gateway.pending_lifecycle import terminate_pending
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.conftest import make_registry_with_loopback
from tests.test_gateway_notify_human import RecordingBackend

_FE_SENTINEL = "__CONVERSATION_ENDED__\n(force-ended)"
_CWD = "c:/work/sw"
_SENDER = "Claude"


@pytest.mark.asyncio
async def test_terminate_pending_sets_result_not_cancelled():
	registry = Registry()
	conv = Conversation(id="conv-fe", title="FE")
	registry.conversations["conv-fe"] = conv
	future = registry.add("conv-fe", "s-1", "Claude", request_id="req-1", msg_id="msg-1")
	assert not future.done()

	record = registry.find_by_request_id("conv-fe", "req-1")
	popped = await terminate_pending(
		registry, backend=None, logger=None, record=record,
		resolve_text=_FE_SENTINEL, remember_resolved=True,
	)

	assert popped is True
	assert future.done()
	assert not future.cancelled(), "force-end must resolve, not cancel, the pending future"
	assert future.result() == _FE_SENTINEL
	# No pending entry survives for the conversation.
	assert registry.pending_for_conversation("conv-fe") == []
	assert registry.was_recently_resolved("conv-fe", "req-1")


@pytest.fixture
def cfg(tmp_path):
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_ask_human_returns_force_end_sentinel_without_treating_as_answer(cfg, logger):
	"""When force-end resolves the blocked future with the __CONVERSATION_ENDED__
	sentinel, ask_human must return it verbatim and NOT run the answered-path
	side effects (resolution confirmation), which would imply John answered."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()  # away ON -> ask_human blocks
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-fe-sentinel-001"
	task = asyncio.create_task(
		handlers.ask_human("Proceed?", _SENDER, cli_session_id=_SID, cwd=_CWD)
	)
	# Let the handler reach wait_for and register the pending entry.
	for _ in range(6):
		await asyncio.sleep(0)
	assert registry.pending_count == 1

	conv_id = registry.session_to_conversation_id.get(_SID)
	for record in registry.pending_for_conversation(conv_id):
		await terminate_pending(
			registry, backend, logger, record,
			resolve_text=_FE_SENTINEL, remember_resolved=True,
		)
	result = await asyncio.wait_for(task, timeout=1.0)

	data = json.loads(result)
	assert data["status"] == "conversation_ended"
	assert data["cause"] == "force-ended"
	# A force-end is not an answer: no resolution confirmation must be sent.
	assert backend.sent_confirmations == []


@pytest.mark.asyncio
async def test_ask_human_bound_to_ended_conversation_returns_sentinel_without_minting(cfg, logger):
	"""Defensive guard (T-145): if an agent retries ask_human in the race window
	where its conversation was force-ended but session-fallback has not yet
	rebound it, the session is still bound to the Ended conversation. ask_human
	must return the terminal sentinel rather than minting orphan state or
	re-adding the session as a member of the Ended conversation."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()  # away ON
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	conv = Conversation(id="conv-ended", title="Dead")
	conv.state = "ended"
	registry.conversations["conv-ended"] = conv
	_SID = "s-ended-001"
	registry.bind_session(_SID, "conv-ended")

	result = await handlers.ask_human("Proceed?", _SENDER, cli_session_id=_SID, cwd=_CWD)

	data = json.loads(result)
	assert data["status"] == "conversation_ended"
	assert data["cause"] == "force-ended"
	# No pending question registered.
	assert registry.pending_count == 0
	# No orphan conversation minted: only the Ended one exists.
	assert list(registry.conversations) == ["conv-ended"]
	# The session was not re-added as a member of the Ended conversation.
	assert conv.members_active == {}
	# No question write went out.
	assert backend.sent_questions == []
