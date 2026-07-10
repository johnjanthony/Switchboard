"""REV-002: an ask_human that passed the away gate but suspended in its
question write while an away-mode exit flipped the flag and drained the
pending snapshot must not strand until the 24h timeout. It withdraws its
just-registered pending and returns the at-desk sentinel - unless the drain
already resolved its future, in which case John's bulk reply wins."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from server.config import Config
from server.gateway import build_tool_handlers
from server.gateway.dispatch import dispatch_away_mode_commands
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.conftest import make_active_conversation
from tests.test_gateway_notify_human import RecordingBackend
from tests.test_dispatch_away_mode_commands import _now_iso

AT_DESK_SENTINEL = "ERROR: John is at his desk. Ask this question via the terminal."
AT_DESK_NOTICE = "John is back at his desk; your question was not answered remotely. Re-ask in the terminal."


class GatedBackend(RecordingBackend):
	"""RecordingBackend that can park an ask_human mid-flight at two await
	points - the question write and (opt-in) the supersede mark-cancelled -
	so a test can run an away-mode exit inside the exact REV-002 windows."""

	def __init__(self):
		super().__init__()
		self.question_write_gate = asyncio.Event()
		self.question_write_entered = asyncio.Event()
		self.mark_gate: asyncio.Event | None = None
		self.mark_entered = asyncio.Event()
		self.cancelled_questions: list[tuple] = []
		self.away_mode_writes: list[bool] = []

	async def set_global_away_mode(self, value: bool) -> None:
		# RecordingBackend has no away-mode mirror; the dispatch exit arm calls
		# this directly on commit, so record it instead of taking the
		# persist-failed exception path (keeps the JSONL output pristine).
		self.away_mode_writes.append(value)

	async def write_conversation_message(self, conv_id, sender_or_message, message_type=None, text=None, **kwargs):
		if message_type == "question":
			self.question_write_entered.set()
			await self.question_write_gate.wait()
		return await super().write_conversation_message(conv_id, sender_or_message, message_type, text, **kwargs)

	async def mark_question_cancelled(self, conversation_id, request_id):
		if self.mark_gate is not None:
			self.mark_entered.set()
			await self.mark_gate.wait()
		self.cancelled_questions.append((conversation_id, request_id))


def _handlers(tmp_path: Path, registry: Registry, backend: RecordingBackend):
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=5.0,
		log_path=str(tmp_path / "server.log"),
	)
	return build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))


def _setup(tmp_path, conv_id, sid):
	registry = Registry()
	registry.global_away_mode = True
	conv = make_active_conversation(conversation_id=conv_id, member_session_id=sid, sender="Claude")
	registry.conversations[conv_id] = conv
	registry.bind_session(sid, conv_id)
	backend = GatedBackend()
	handlers = _handlers(tmp_path, registry, backend)
	return registry, backend, handlers


def _make_supervisor():
	supervisor = MagicMock()
	supervisor.record_success = MagicMock()
	supervisor.record_crash = AsyncMock()
	return supervisor


@pytest.mark.asyncio
async def test_phone_exit_racing_in_flight_ask_does_not_strand(tmp_path):
	# REV-002 mainline repro: agent calls ask_human in away mode; while the
	# question write is in flight, John taps Exit on the phone (skip). The
	# ask registers after the drain snapshot; without the post-add re-check
	# it would wait the full timeout. It must return the at-desk sentinel
	# promptly, withdraw the pending, and cancel the question in Firebase.
	registry, backend, handlers = _setup(tmp_path, "conv-r1", "s-r1")

	ask_task = asyncio.create_task(
		handlers.ask_human("Deploy?", "Claude", cli_session_id="s-r1", cwd="C:/Work/X")
	)
	await asyncio.wait_for(backend.question_write_entered.wait(), timeout=2.0)

	async def _poll():
		yield {"type": "exit_global", "issued_at": _now_iso(), "decision": "skip"}
		raise asyncio.CancelledError()

	backend.poll_away_mode_commands = _poll
	with pytest.raises(asyncio.CancelledError):
		await dispatch_away_mode_commands(
			registry, backend, JsonlLogger(str(tmp_path / "d.jsonl")), _make_supervisor()
		)
	assert registry.global_away_mode is False

	backend.question_write_gate.set()
	result = await asyncio.wait_for(ask_task, timeout=2.0)
	assert result == AT_DESK_SENTINEL
	assert registry.pending_count == 0
	assert len(backend.cancelled_questions) == 1


@pytest.mark.asyncio
async def test_tool_exit_racing_in_flight_ask_does_not_strand(tmp_path):
	# Same window, tool-side driver: set_away_mode(False) flips first and
	# drains, but this ask is not in its snapshot either. The post-add
	# re-check must catch it identically.
	registry, backend, handlers = _setup(tmp_path, "conv-r2", "s-r2")

	ask_task = asyncio.create_task(
		handlers.ask_human("Deploy?", "Claude", cli_session_id="s-r2", cwd="C:/Work/X")
	)
	await asyncio.wait_for(backend.question_write_entered.wait(), timeout=2.0)

	result_exit = await handlers.set_away_mode(False, cli_session_id="s-r2", cwd="C:/Work/X")
	assert result_exit.startswith("ok")
	assert registry.global_away_mode is False

	backend.question_write_gate.set()
	result = await asyncio.wait_for(ask_task, timeout=2.0)
	assert result == AT_DESK_SENTINEL
	assert registry.pending_count == 0
	assert len(backend.cancelled_questions) == 1


@pytest.mark.asyncio
async def test_drain_caught_ask_returns_bulk_reply_not_sentinel(tmp_path):
	# Fall-through guard (decided semantics #4): if the drain's snapshot DID
	# catch this pending (supersede-cleanup await suspends between add and
	# the re-check), the future already holds John's bulk text - which may
	# be a real answer. The re-check must fall through and return it, not
	# discard it for the sentinel.
	registry, backend, handlers = _setup(tmp_path, "conv-r3", "s-r3")

	# A prior pending from the same session forces the supersede-cleanup
	# await; gate it so the exit can run between add and the re-check.
	registry.add("conv-r3", "s-r3", "Claude", request_id="req-old", msg_id="msg-old")
	backend.mark_gate = asyncio.Event()
	backend.question_write_gate.set()  # question write passes straight through

	ask_task = asyncio.create_task(
		handlers.ask_human("Deploy?", "Claude", cli_session_id="s-r3", cwd="C:/Work/X")
	)
	# Parked inside _safe_mark_cancelled(req-old): registry.add has run.
	await asyncio.wait_for(backend.mark_entered.wait(), timeout=2.0)
	assert registry.pending_count == 1

	# Exit drains now - the snapshot INCLUDES the new pending and resolves it.
	result_exit = await handlers.set_away_mode(False, cli_session_id="s-r3", cwd="C:/Work/X")
	assert "resolved" in result_exit
	assert registry.global_away_mode is False

	backend.mark_gate.set()
	result = await asyncio.wait_for(ask_task, timeout=2.0)
	assert result == AT_DESK_NOTICE, "drain-resolved ask must return John's bulk reply, not the sentinel"
	assert registry.pending_count == 0
