"""P0-6 acceptance: fresh-spawned agents become conversation members on their
first switchboard tool call, so SessionEnd dormancy, phone Resume, and
combine-relaunch work for the primary phone spawn flow.

Regression for the 2026-06-12 live-smoke finding: handle_fresh binds the
session but never creates a ConversationMember; cli_session_end then finds no
member and silently no-ops, so the member never goes dormant."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, Registry
from tests.test_gateway_notify_human import RecordingBackend


def _cfg(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.mark.asyncio
async def test_resolver_adds_member_for_bound_memberless_conv():
	"""The fresh-spawn state: conversation present, session bound, no member.
	The resolver adds the member named for the caller's sender."""
	from server.conversation_ops import _resolve_conversation_and_member
	registry = Registry()
	conv = Conversation(id="conv-fresh", title="modules (windows)")
	registry.conversations["conv-fresh"] = conv
	registry.bind_session("s-fresh", "conv-fresh")
	assert conv.members_active == {}

	result = await _resolve_conversation_and_member(
		registry, "s-fresh", "C:/Work/modules", "Claude Win",
	)

	assert result == "conv-fresh"
	members = [m for m in conv.members_active.values() if m.cli_session_id == "s-fresh"]
	assert len(members) == 1
	assert members[0].sender == "Claude Win"


@pytest.mark.asyncio
async def test_resolver_idempotent_no_duplicate_member():
	"""Two sequential resolves for the same bound session add exactly one member."""
	from server.conversation_ops import _resolve_conversation_and_member
	registry = Registry()
	conv = Conversation(id="conv-fresh", title="t")
	registry.conversations["conv-fresh"] = conv
	registry.bind_session("s-fresh", "conv-fresh")

	await _resolve_conversation_and_member(registry, "s-fresh", "C:/Work/modules", "Claude Win")
	await _resolve_conversation_and_member(registry, "s-fresh", "C:/Work/modules", "Claude Win")

	members = [m for m in conv.members_active.values() if m.cli_session_id == "s-fresh"]
	assert len(members) == 1


@pytest.mark.asyncio
async def test_resolver_bound_but_conv_missing_returns_id_unchanged():
	"""Defensive edge: bound to a conv_id with no Conversation object. Return
	the id unchanged; do NOT mint (that would break legacy routing for
	test_notify_human_routes_to_existing_conversation)."""
	from server.conversation_ops import _resolve_conversation_and_member
	registry = Registry()
	registry.bind_session("s-ghost", "conv-ghost")  # no Conversation object exists

	result = await _resolve_conversation_and_member(registry, "s-ghost", "C:/Work/x", "Claude")

	assert result == "conv-ghost"
	assert "conv-ghost" not in registry.conversations


@pytest.mark.asyncio
async def test_resolver_unbound_no_mint_returns_none():
	"""mint_if_unbound=False (the message_and_await_agent contract): a truly
	unbound session resolves to None, mints nothing."""
	from server.conversation_ops import _resolve_conversation_and_member
	registry = Registry()

	result = await _resolve_conversation_and_member(
		registry, "s-none", "C:/Work/x", "Claude", mint_if_unbound=False,
	)

	assert result is None
	assert registry.session_to_conversation_id.get("s-none") is None
	assert len(registry.conversations) == 0


@pytest.mark.asyncio
async def test_fresh_spawn_first_ask_human_creates_member(tmp_path):
	"""Fresh-spawn state + first ask_human: a member is created for the bound
	session so the conversation is no longer member-less."""
	backend = RecordingBackend()
	registry = Registry()
	registry.global_away_mode = True  # so ask_human blocks rather than at-desk-redirecting
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	conv = Conversation(id="conv-fresh", title="modules (windows)")
	registry.conversations["conv-fresh"] = conv
	registry.bind_session("s-fresh", "conv-fresh")
	assert conv.members_active == {}
	handlers = build_tool_handlers(_cfg(tmp_path), registry, backend, logger)

	task = asyncio.create_task(
		handlers.ask_human("Ready?", "Claude Win", cli_session_id="s-fresh", cwd="C:/Work/modules")
	)
	await asyncio.sleep(0)
	await asyncio.sleep(0)

	member = next((m for m in conv.members_active.values() if m.cli_session_id == "s-fresh"), None)
	assert member is not None, "first ask_human must create the member for the bound session"
	assert member.sender == "Claude Win"

	# Unblock the handler so the task completes cleanly.
	pending = registry.pending_for_conversation("conv-fresh")[0]
	req_id = registry.resolve(conversation_id="conv-fresh", request_id=pending.request_id, text="go")
	assert req_id is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "go"


@pytest.mark.asyncio
async def test_fresh_spawn_then_session_end_marks_dormant(tmp_path):
	"""The exact bug tonight's smoke caught, end to end: a fresh-spawn member is
	created on first tool call, then SessionEnd marks it dormant (not a silent
	no-op). Uses notify_human (non-blocking) to establish membership."""
	from server.cli_session_end import handle_session_end
	backend = RecordingBackend()
	registry = Registry()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	conv = Conversation(id="conv-fresh", title="modules (windows)")
	registry.conversations["conv-fresh"] = conv
	registry.bind_session("s-fresh", "conv-fresh")
	handlers = build_tool_handlers(_cfg(tmp_path), registry, backend, logger)

	result = await handlers.notify_human("online", "Claude Win", cli_session_id="s-fresh", cwd="C:/Work/modules")
	assert result == "ERROR: John is at his desk (notification delivered to phone anyway)."
	member = next((m for m in conv.members_active.values() if m.cli_session_id == "s-fresh"), None)
	assert member is not None and member.alive is True

	await handle_session_end(
		registry=registry, session_id="s-fresh", reason="logout",
		now=lambda: "2026-06-12T00:00:00Z",
	)

	assert member.alive is False
	assert member.session_end_reason == "logout"
	assert member.session_lost_permanently is False


@pytest.mark.asyncio
async def test_fresh_spawn_message_and_await_ensures_membership(tmp_path):
	"""Fresh-spawn state + message_and_await_agent: membership is ensured (the
	'session bound but not a member' error becomes unreachable)."""
	backend = RecordingBackend()
	registry = Registry()
	registry.global_away_mode = True
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	conv = Conversation(id="conv-fresh", title="modules (windows)")
	registry.conversations["conv-fresh"] = conv
	registry.bind_session("s-fresh", "conv-fresh")
	handlers = build_tool_handlers(_cfg(tmp_path), registry, backend, logger)

	task = asyncio.create_task(
		handlers.message_and_await_agent("Claude Win", message="hello peers", cli_session_id="s-fresh", cwd="C:/Work/modules")
	)
	await asyncio.sleep(0)
	await asyncio.sleep(0)

	member = next((m for m in conv.members_active.values() if m.cli_session_id == "s-fresh"), None)
	assert member is not None, "message_and_await_agent must ensure membership, not error out"
	assert member.sender == "Claude Win"

	# Sole member with no peer: the handler parks in wait_queue. Cancel to finish.
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


class _CapturingLogger:
	"""Minimal logger that records surface_error calls for assertions."""
	def __init__(self) -> None:
		self.errors: list[str] = []

	async def surface_error(self, msg, **kwargs) -> None:
		self.errors.append(msg)


@pytest.mark.asyncio
async def test_session_end_no_member_logs_loudly():
	"""A bound session whose conversation has no matching member must log loudly
	on the silent-skip path instead of returning silently (the invisibility that
	hid the membership-gap bug)."""
	from server.cli_session_end import handle_session_end
	registry = Registry()
	conv = Conversation(id="conv-x", title="t")
	registry.conversations["conv-x"] = conv
	registry.bind_session("s-x", "conv-x")  # bound, but conv has no member
	logger = _CapturingLogger()

	await handle_session_end(
		registry=registry, session_id="s-x", reason="logout",
		now=lambda: "2026-06-12T00:00:00Z", logger=logger,
	)

	assert any("session_end_no_member" in e for e in logger.errors), \
		f"expected a loud no-member log; got: {logger.errors}"


@pytest.mark.asyncio
async def test_fresh_prompt_mentions_membership_registration(tmp_path):
	"""The fresh-spawn (non-join) prompt states that the first switchboard call
	registers membership."""
	from unittest.mock import AsyncMock
	from server.logging_jsonl import JsonlLogger
	from server.registry import Conversation
	from server.spawn import SpawnHandler

	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=tmp_path,
	)
	handler = SpawnHandler(cfg, AsyncMock(), JsonlLogger(cfg.log_path), Registry())
	conv = Conversation(id="conv-fresh", title="modules (windows)")

	prompt = handler._format_fresh_prompt({"prompt": None}, conv, join_existing=False)

	assert "registers you as a member" in prompt
