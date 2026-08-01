"""Tests for the shared inbound human-to-agent delivery ladder."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.registry import Registry, Conversation, ConversationMember
from server.inbound import deliver_human_message, deliver_background_answer


def _conv(registry, conv_id="conv-1", members=()):
	conv = Conversation(id=conv_id, title="t", state="active")
	for sid, sender in members:
		conv.members_active[sid] = ConversationMember(
			cli_session_id=sid, sender=sender, cwd="c:/w", surface="windows", joined_at=0.0,
		)
	registry.conversations[conv_id] = conv
	return conv


def _backend():
	backend = MagicMock()
	backend.write_conversation_message = AsyncMock(return_value=("corr", "msg-1"))
	backend.set_conversation_last_activity = AsyncMock()
	backend.mark_question_cancelled = AsyncMock()
	backend.remove_pending_question_record = AsyncMock()
	backend.send_text = AsyncMock()
	return backend


async def _pump():
	for _ in range(5):
		await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_message_resolves_live_blocking_ask(tmp_path):
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	future = registry.add("conv-1", "sess-1", "Agent", "req-1")
	registry.find_by_request_id("conv-1", "req-1").notices.append("earlier notice")
	backend = _backend()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	result = await deliver_human_message(registry, backend, None, logger, "conv-1", "stop, build is red")
	await _pump()
	assert result["resolved"] == ["sess-1"]
	assert future.done()
	assert future.result() == "earlier notice\n\n[John interjected - not a direct answer to your question] stop, build is red"
	assert registry.find_by_request_id("conv-1", "req-1") is None
	backend.mark_question_cancelled.assert_awaited_with("conv-1", "req-1")


@pytest.mark.asyncio
async def test_message_wakes_all_parked_waiters(tmp_path):
	"""Two members parked in wait_queue both wake (wake-all, not FIFO-one), and
	their payload contains the human message line."""
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent-1"), ("sess-2", "Agent-2")])
	futures = {}
	for sid, sender in [("sess-1", "Agent-1"), ("sess-2", "Agent-2")]:
		member = conv.members_active[sid]
		fut = asyncio.get_event_loop().create_future()
		futures[sid] = fut
		conv.wait_queue.append({
			"member": member, "future": fut, "waiting_kind": "msg_and_await", "block_position": 0.0,
		})
	backend = _backend()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	result = await deliver_human_message(registry, backend, None, logger, "conv-1", "build is red")
	await _pump()
	assert result["resolved"] == []
	assert sorted(result["woken"]) == ["sess-1", "sess-2"]
	assert len(conv.wait_queue) == 0
	for sid, fut in futures.items():
		assert fut.done()
		assert "John: build is red" in fut.result()
		assert conv.members_active[sid].last_seen_seq == len(conv.messages)


@pytest.mark.asyncio
async def test_message_notices_working_member(tmp_path):
	"""A member with neither a live blocking ask nor a wait_queue entry gets a
	queued session notice."""
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	backend = _backend()
	session_registry = MagicMock()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	result = await deliver_human_message(registry, backend, session_registry, logger, "conv-1", "ping")
	await _pump()
	assert result["noticed"] == ["sess-1"]
	session_registry.queue_notice.assert_called_once_with("sess-1", "John (from phone): ping")


@pytest.mark.asyncio
async def test_message_rung_exclusivity_across_three_members(tmp_path):
	"""Pins rung exclusivity: a member resolved at rung 1 or woken at rung 2 must
	not also be noticed at rung 3. Three live members share one conversation: one
	blocked in a live ask (rung 1), one parked in wait_queue (rung 2), one merely
	working with neither (rung 3). queue_notice must fire exactly once, and only
	for the working member's session id."""
	registry = Registry()
	conv = _conv(registry, members=[
		("sess-1", "Blocked-Agent"), ("sess-2", "Parked-Agent"), ("sess-3", "Working-Agent"),
	])
	future = registry.add("conv-1", "sess-1", "Blocked-Agent", "req-1")
	parked_member = conv.members_active["sess-2"]
	parked_future = asyncio.get_event_loop().create_future()
	conv.wait_queue.append({
		"member": parked_member, "future": parked_future, "waiting_kind": "msg_and_await", "block_position": 0.0,
	})
	backend = _backend()
	session_registry = MagicMock()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	result = await deliver_human_message(registry, backend, session_registry, logger, "conv-1", "status check")
	await _pump()
	assert future.done()
	assert parked_future.done()
	assert result["resolved"] == ["sess-1"]
	assert result["woken"] == ["sess-2"]
	assert result["noticed"] == ["sess-3"]
	session_registry.queue_notice.assert_called_once_with("sess-3", "John (from phone): status check")


@pytest.mark.asyncio
async def test_message_notice_gated_on_queue_notice_return_value(tmp_path):
	"""Pins the approved deviation at inbound.py's rung-3 queue_notice call: a
	live working member's sid is appended to noticed only if queue_notice
	returns truthy. SessionRegistry.queue_notice returns False for a session
	absent from the registry, so that case must leave noticed empty even though
	delivered stays True. This is the named test coverage for the project-lead
	approved deviation from the plan's unconditional-append text."""
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	backend = _backend()
	session_registry = MagicMock()
	session_registry.queue_notice.return_value = False
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	result = await deliver_human_message(registry, backend, session_registry, logger, "conv-1", "ping")
	await _pump()
	assert result["delivered"] is True
	assert result["noticed"] == []
	session_registry.queue_notice.assert_called_once_with("sess-1", "John (from phone): ping")


@pytest.mark.asyncio
async def test_message_skips_dormant_member(tmp_path):
	"""A dormant (alive=False) member gets no rung at all."""
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	conv.members_active["sess-1"].alive = False
	backend = _backend()
	session_registry = MagicMock()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	result = await deliver_human_message(registry, backend, session_registry, logger, "conv-1", "ping")
	await _pump()
	assert result == {"delivered": True, "resolved": [], "woken": [], "noticed": []}
	session_registry.queue_notice.assert_not_called()


@pytest.mark.asyncio
async def test_message_unknown_or_ended_conversation_returns_not_delivered(tmp_path):
	registry = Registry()
	backend = _backend()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))

	result = await deliver_human_message(registry, backend, None, logger, "conv-missing", "ping")
	assert result == {"delivered": False, "resolved": [], "woken": [], "noticed": []}
	backend.write_conversation_message.assert_not_awaited()

	conv = _conv(registry, conv_id="conv-ended", members=[("sess-1", "Agent")])
	conv.state = "ended"
	result = await deliver_human_message(registry, backend, None, logger, "conv-ended", "ping")
	await _pump()
	assert result == {"delivered": False, "resolved": [], "woken": [], "noticed": []}
	assert conv.messages == []
	backend.write_conversation_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_appended_to_history_and_written_with_suppress_push(tmp_path):
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	backend = _backend()
	session_registry = MagicMock()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	pre_len = len(conv.messages)
	await deliver_human_message(registry, backend, session_registry, logger, "conv-1", "hi there")
	await _pump()
	assert len(conv.messages) == 1
	msg = conv.messages[0]
	assert msg["type"] == "human"
	assert msg["sender"] == "John"
	assert msg["text"] == "hi there"
	assert msg["seq"] == pre_len
	assert isinstance(msg["timestamp"], str) and msg["timestamp"] != ""
	backend.write_conversation_message.assert_awaited_once_with(
		"conv-1", "John", "human", "hi there", format="markdown", suppress_push=True,
	)


@pytest.mark.asyncio
async def test_background_answer_rung1_resolves_live_ask(tmp_path):
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	future = registry.add("conv-1", "sess-1", "Agent", "req-live")
	request_id, _ = registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="are you ok?")
	record = registry.find_by_request_id("conv-1", "req-bg")
	backend = _backend()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	rung = await deliver_background_answer(registry, backend, None, logger, record, "yes, fine")
	assert rung == "resolved_ask"
	assert future.done()
	assert future.result() == "John answered your earlier question 'are you ok?': yes, fine"
	backend.remove_pending_question_record.assert_awaited_once_with("conv-1", "req-bg")
	backend.mark_question_cancelled.assert_awaited_once_with("conv-1", "req-live")
	assert registry.find_by_request_id("conv-1", "req-live") is None


@pytest.mark.asyncio
async def test_background_answer_rung2_wakes_wait_queue_without_advancing_cursor(tmp_path):
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	member = conv.members_active["sess-1"]
	member.last_seen_seq = 3
	fut = asyncio.get_event_loop().create_future()
	conv.wait_queue.append({
		"member": member, "future": fut, "waiting_kind": "msg_and_await", "block_position": 0.0,
	})
	request_id, _ = registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="are you ok?")
	record = registry.find_by_request_id("conv-1", "req-bg")
	backend = _backend()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	rung = await deliver_background_answer(registry, backend, None, logger, record, "yes, fine")
	assert rung == "woke_wait"
	assert fut.done()
	assert fut.result() == "John answered your earlier question 'are you ok?': yes, fine"
	assert len(conv.wait_queue) == 0
	assert member.last_seen_seq == 3
	backend.remove_pending_question_record.assert_awaited_once_with("conv-1", "req-bg")
	backend.mark_question_cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_answer_rung3_queues_notices_then_answer(tmp_path):
	registry = Registry()
	conv = _conv(registry, members=[("sess-1", "Agent")])
	request_id, _ = registry.add_background("conv-1", "sess-1", "Agent", "req-bg", question="are you ok?")
	record = registry.find_by_request_id("conv-1", "req-bg")
	record.notices.append("earlier convene notice")
	backend = _backend()
	session_registry = MagicMock()
	from server.logging_jsonl import JsonlLogger
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	rung = await deliver_background_answer(registry, backend, session_registry, logger, record, "yes, fine")
	assert rung == "queued_notice"
	calls = session_registry.queue_notice.call_args_list
	assert calls[0].args == ("sess-1", "earlier convene notice")
	assert calls[1].args == ("sess-1", "John answered your earlier question 'are you ok?': yes, fine")
	backend.remove_pending_question_record.assert_awaited_once_with("conv-1", "req-bg")
	backend.mark_question_cancelled.assert_not_awaited()
