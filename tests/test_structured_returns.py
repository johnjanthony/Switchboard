"""Envelope helpers: internal sentinels stay strings; tool returns become one-line JSON."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.gateway.handlers import _envelope, _terminal_envelope, TIMEOUT_SENTINEL
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend
from tests.test_message_and_await_agent import (
	_make_registry_with_one_alive_member,
	_make_registry_with_two_alive_members,
)


def _cfg(tmp_path, timeout_seconds: float = 5.0) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=timeout_seconds,
		log_path=str(tmp_path / "log.jsonl"),
	)


def test_envelope_is_one_line_json_with_status_first():
	raw = _envelope("ok", conversation_id="conv-1", peers=["A"])
	assert "\n" not in raw
	data = json.loads(raw)
	assert data == {"status": "ok", "conversation_id": "conv-1", "peers": ["A"]}
	assert list(data.keys())[0] == "status"

def test_envelope_omits_none_fields():
	data = json.loads(_envelope("ok", conversation_id="conv-1", log=None))
	assert "log" not in data

def test_terminal_envelope_timeout():
	data = json.loads(_terminal_envelope(TIMEOUT_SENTINEL))
	assert data == {"status": "timeout"}

def test_terminal_envelope_conversation_ended_with_cause():
	data = json.loads(_terminal_envelope("__CONVERSATION_ENDED__\n(force-ended)"))
	assert data == {"status": "conversation_ended", "cause": "force-ended"}
	data = json.loads(_terminal_envelope("__CONVERSATION_ENDED__\n(merged into target)"))
	assert data["cause"] == "merged into target"

def test_terminal_envelope_none_for_normal_text():
	assert _terminal_envelope("just a payload") is None
	assert _terminal_envelope("") is None


@pytest.mark.asyncio
async def test_message_and_await_wake_returns_ok_envelope(tmp_path):
	"""A speaks (blocks). B speaks -> A wakes with an ok envelope carrying B's line."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_two_alive_members()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(_cfg(tmp_path), r, backend, logger)

	task_a = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-A", message="hello from A", cli_session_id="s-A", cwd="C:/X",
	))
	await asyncio.sleep(0.05)

	task_b = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-B", message="hi back from B", cli_session_id="s-B", cwd="C:/Y",
	))

	result_a = await asyncio.wait_for(task_a, timeout=2.0)
	data = json.loads(result_a)
	assert data["status"] == "ok"
	assert data["conversation_id"] == conv_id
	assert "Claude-B: hi back from B" in data["log"]

	task_b.cancel()
	try:
		await task_b
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_message_and_await_timeout_returns_timeout_envelope(tmp_path):
	"""With a tiny config.timeout_seconds, the sole waiter times out -> {"status": "timeout"}."""
	backend = RecordingBackend()
	r, _conv_id = _make_registry_with_two_alive_members()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(_cfg(tmp_path, timeout_seconds=0.1), r, backend, logger)

	result = await handlers.message_and_await_agent(
		"Claude-A", message="will timeout", cli_session_id="s-A", cwd="C:/X",
	)

	assert json.loads(result) == {"status": "timeout"}


@pytest.mark.asyncio
async def test_message_and_await_sole_alive_parks_then_wakes_ok(tmp_path):
	"""Sole-alive member parks in the wait_queue instead of getting a
	conversation_empty envelope; a joiner's speak wakes it with an ok envelope.
	The conversation_empty status no longer appears anywhere in the flow."""
	backend = RecordingBackend()
	r, conv_id = _make_registry_with_one_alive_member()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(_cfg(tmp_path), r, backend, logger)

	solo_task = asyncio.create_task(handlers.message_and_await_agent(
		"Claude-Solo", message="still here?", cli_session_id="s-solo", cwd="C:/Z",
	))
	for _ in range(5):
		await asyncio.sleep(0)

	conv = r.conversations[conv_id]
	assert len(conv.wait_queue) == 1
	assert "s-solo" in conv.members_active

	join_result = await handlers.join_conversation(
		"Joiner", ref=conv_id, cli_session_id="s-joiner", cwd="/home/j",
	)
	assert "conversation_empty" not in join_result

	speak_task = asyncio.create_task(handlers.message_and_await_agent(
		"Joiner", message="hi there", cli_session_id="s-joiner", cwd="/home/j",
	))

	result = await asyncio.wait_for(solo_task, timeout=2.0)
	data = json.loads(result)
	assert data["status"] == "ok"
	assert "hi there" in data["log"]
	assert "conversation_empty" not in result

	speak_task.cancel()
	try:
		await speak_task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_leave_returns_ok_envelope(tmp_path):
	"""leave_conversation's success return is an ok envelope with conversation_id."""
	backend = RecordingBackend()
	r = Registry()
	mA = ConversationMember(cli_session_id="s-A", sender="Claude-A", cwd="C:/X", surface="windows", joined_at=time.time())
	mB = ConversationMember(cli_session_id="s-B", sender="Claude-B", cwd="C:/Y", surface="windows", joined_at=time.time())
	conv = Conversation(id="conv-leave", title="leave test")
	conv.members_active["s-A"] = mA
	conv.members_active["s-B"] = mB
	r.conversations["conv-leave"] = conv
	r.bind_session("s-A", "conv-leave")
	r.bind_session("s-B", "conv-leave")
	r.set_session_home("s-A", "conv-leave")
	r.set_session_home("s-B", "conv-leave")
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(_cfg(tmp_path), r, backend, logger)

	result = await handlers.leave_conversation(
		"Claude-A", "signing off", cli_session_id="s-A", cwd="C:/X",
	)

	assert json.loads(result) == {"status": "ok", "conversation_id": "conv-leave"}


@pytest.mark.asyncio
async def test_combine_returns_ok_envelope(tmp_path):
	"""combine_conversations's success return carries status, source, target, detail."""
	backend = RecordingBackend()
	r = Registry()
	src = Conversation(id="conv-src9", title="Source")
	mA = ConversationMember(cli_session_id="s-A9", sender="Claude-A9", cwd="C:/X", surface="windows", joined_at=time.time())
	src.members_active["s-A9"] = mA
	r.conversations["conv-src9"] = src
	r.bind_session("s-A9", "conv-src9")
	r.set_session_home("s-A9", "conv-src9")

	tgt = Conversation(id="conv-tgt9", title="Target")
	mT = ConversationMember(cli_session_id="s-T9", sender="Claude-T9", cwd="C:/Y", surface="windows", joined_at=time.time())
	tgt.members_active["s-T9"] = mT
	r.conversations["conv-tgt9"] = tgt
	r.bind_session("s-T9", "conv-tgt9")
	r.set_session_home("s-T9", "conv-tgt9")

	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(_cfg(tmp_path), r, backend, logger)

	result = await handlers.combine_conversations(
		"conv-src9", "conv-tgt9", cli_session_id="s-T9", cwd="C:/Y",
	)

	data = json.loads(result)
	assert data["status"] == "ok"
	assert data["source"] == "conv-src9"
	assert data["target"] == "conv-tgt9"
	assert isinstance(data["detail"], str) and data["detail"]


@pytest.mark.asyncio
async def test_lookup_returns_ok_envelope(tmp_path):
	"""lookup_conversation_ids's success return carries status and conversation_ids."""
	backend = RecordingBackend()
	r = Registry()
	m = ConversationMember(cli_session_id="s-look", sender="Claude-Look", cwd="C:/X", surface="windows", joined_at=0.0)
	conv = Conversation(id="conv-look", title="lookup test")
	conv.members_active["s-look"] = m
	r.conversations["conv-look"] = conv
	r.bind_session("s-look", "conv-look")

	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	handlers = build_tool_handlers(_cfg(tmp_path), r, backend, logger)

	result = await handlers.lookup_conversation_ids(
		title_contains="lookup", cli_session_id="s-look", cwd="C:/X",
	)

	assert json.loads(result) == {"status": "ok", "conversation_ids": ["conv-look"]}
