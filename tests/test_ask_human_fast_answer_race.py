"""A fast answer - one that lands while ask_human is still suspended inside
the question write (unread bump, meta update, FCM send are real Firebase
round-trips) - must resolve the ask, not be discarded as an unknown
correlation. Register-first makes the window zero-width by construction:
registry.add is synchronous and precedes the write."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from server.registry import Registry
from tests.test_ask_human_away_exit_race import GatedBackend, _handlers, _setup


@pytest.mark.asyncio
async def test_pending_registered_before_question_write(tmp_path):
	registry, backend, handlers = _setup(tmp_path, "conv-race", "sess-race")
	task = asyncio.create_task(handlers.ask_human(
		"fast?", "Claude", cli_session_id="sess-race", cwd="C:/Work/X",
	))
	await asyncio.wait_for(backend.question_write_entered.wait(), 5)
	# The ask is parked INSIDE the write; the correlation must already exist.
	assert registry.pending_count == 1
	record = registry.pending_for_conversation("conv-race")[0]
	assert record.msg_id is None  # patched only after the write returns
	request_id = record.request_id
	backend.question_write_gate.set()
	# Resolve normally so the task ends; msg_id must have been patched.
	for _ in range(5):
		await asyncio.sleep(0)
	patched = registry.find_by_request_id("conv-race", request_id)
	assert patched is not None and patched.msg_id is not None
	assert registry.resolve("conv-race", request_id, "answer") == request_id
	assert await asyncio.wait_for(task, 5) == "answer"


@pytest.mark.asyncio
async def test_answer_landing_mid_write_resolves_the_ask(tmp_path):
	registry, backend, handlers = _setup(tmp_path, "conv-race2", "sess-race2")
	backend.add_pending_question_record = AsyncMock()
	task = asyncio.create_task(handlers.ask_human(
		"fast?", "Claude", cli_session_id="sess-race2", cwd="C:/Work/X",
	))
	await asyncio.wait_for(backend.question_write_entered.wait(), 5)
	record = registry.pending_for_conversation("conv-race2")[0]
	# The fast answer: resolves through the same registry entry dispatch uses.
	assert registry.resolve("conv-race2", record.request_id, "instant reply") is not None
	backend.question_write_gate.set()
	assert await asyncio.wait_for(task, 5) == "instant reply"
	assert registry.pending_count == 0
	# An already-answered ask must not spawn the phone pending record (the
	# spawn is gated on the future still being open).
	for _ in range(5):
		await asyncio.sleep(0)
	backend.add_pending_question_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_failure_withdraws_the_registered_pending(tmp_path):
	registry, backend, handlers = _setup(tmp_path, "conv-fail", "sess-fail")
	backend.write_conversation_message = AsyncMock(side_effect=RuntimeError("rtdb down"))
	result = await handlers.ask_human(
		"doomed?", "Claude", cli_session_id="sess-fail", cwd="C:/Work/X",
	)
	assert result.startswith("ERROR: rtdb down")
	assert registry.pending_count == 0  # compensating terminate_pending ran
	assert any(cid == "conv-fail" for cid, _rid in backend.cancelled_questions)
