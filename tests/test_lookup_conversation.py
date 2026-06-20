"""Tests for lookup_conversation_ids."""

from __future__ import annotations

import asyncio
import json

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from tests.test_gateway_notify_human import RecordingBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _make_member(session_id: str, sender: str, cwd: str, alive: bool = True) -> ConversationMember:
	return ConversationMember(
		cli_session_id=session_id,
		sender=sender,
		cwd=cwd,
		surface="windows",
		joined_at=0.0,
		alive=alive,
	)


def _make_conv(conv_id: str, title: str, *members: ConversationMember, state: str = "active") -> Conversation:
	conv = Conversation(id=conv_id, title=title, state=state)
	for m in members:
		conv.members_active[m.sender] = m
	return conv


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_by_title(cfg, logger):
	"""Filter by title_contains returns only matching active conversation."""
	backend = RecordingBackend()
	registry = Registry()

	m1 = _make_member("s-1", "Claude-A", "C:/X")
	m2 = _make_member("s-2", "Claude-B", "C:/Y")
	conv_match = _make_conv("conv-match", "switchboard project", m1)
	conv_no_match = _make_conv("conv-other", "unrelated session", m2)

	registry.conversations["conv-match"] = conv_match
	registry.conversations["conv-other"] = conv_no_match
	registry.bind_session("s-1", "conv-match")
	registry.bind_session("s-2", "conv-other")

	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.lookup_conversation_ids(
		title_contains="switchboard",
		cli_session_id="s-1",
		cwd="C:/X",
	)

	ids = json.loads(result)
	assert ids == ["conv-match"]


@pytest.mark.asyncio
async def test_lookup_by_sender(cfg, logger):
	"""Filter by sender_contains returns only conversations with a matching member."""
	backend = RecordingBackend()
	registry = Registry()

	m_alpha = _make_member("s-alpha", "Alpha-Agent", "C:/A")
	m_beta = _make_member("s-beta", "Beta-Agent", "C:/B")
	conv_alpha = _make_conv("conv-alpha", "alpha conv", m_alpha)
	conv_beta = _make_conv("conv-beta", "beta conv", m_beta)

	registry.conversations["conv-alpha"] = conv_alpha
	registry.conversations["conv-beta"] = conv_beta
	registry.bind_session("s-alpha", "conv-alpha")
	registry.bind_session("s-beta", "conv-beta")

	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.lookup_conversation_ids(
		sender_contains="alpha",
		cli_session_id="s-alpha",
		cwd="C:/A",
	)

	ids = json.loads(result)
	assert ids == ["conv-alpha"]


@pytest.mark.asyncio
async def test_lookup_by_cwd(cfg, logger):
	"""Filter by cwd_filter returns only conversations with a member at that cwd."""
	backend = RecordingBackend()
	registry = Registry()

	m_x = _make_member("s-x", "Claude-X", "C:/Work/project-x")
	m_y = _make_member("s-y", "Claude-Y", "C:/Work/project-y")
	conv_x = _make_conv("conv-x", "project x", m_x)
	conv_y = _make_conv("conv-y", "project y", m_y)

	registry.conversations["conv-x"] = conv_x
	registry.conversations["conv-y"] = conv_y
	registry.bind_session("s-x", "conv-x")
	registry.bind_session("s-y", "conv-y")

	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.lookup_conversation_ids(
		cwd_filter="C:/Work/project-x",
		cli_session_id="s-x",
		cwd="C:/Work/project-x",
	)

	ids = json.loads(result)
	assert ids == ["conv-x"]


@pytest.mark.asyncio
async def test_lookup_no_filter_errors(cfg, logger):
	"""Calling with no filters returns an ERROR string."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.lookup_conversation_ids(
		cli_session_id="s-any",
		cwd="C:/X",
	)

	assert result.startswith("ERROR")
	assert "required" in result


@pytest.mark.asyncio
async def test_lookup_skips_ended_conversations(cfg, logger):
	"""Ended conversations are excluded even if title matches."""
	backend = RecordingBackend()
	registry = Registry()

	m_active = _make_member("s-active", "Claude-Active", "C:/A")
	m_ended = _make_member("s-ended", "Claude-Ended", "C:/E")
	conv_active = _make_conv("conv-active", "shared title", m_active, state="active")
	conv_ended = _make_conv("conv-ended", "shared title", m_ended, state="ended")

	registry.conversations["conv-active"] = conv_active
	registry.conversations["conv-ended"] = conv_ended
	registry.bind_session("s-active", "conv-active")

	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.lookup_conversation_ids(
		title_contains="shared title",
		cli_session_id="s-active",
		cwd="C:/A",
	)

	ids = json.loads(result)
	assert "conv-active" in ids
	assert "conv-ended" not in ids


@pytest.mark.asyncio
async def test_lookup_missing_cli_session_id_errors(cfg, logger):
	"""Missing cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.lookup_conversation_ids(title_contains="anything")

	assert result.startswith("ERROR: cli_session_id required")
