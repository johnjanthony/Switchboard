"""Tests for the session_id-based routing in ask_human / notify_human / send_document_human handlers.

These verify the routing layer: missing session → auto-create conversation; existing session →
route to bound conversation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


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
async def test_notify_human_auto_creates_conversation_on_first_call(cfg, logger):
	"""When cli_session_id is not yet bound, notify_human auto-creates an Active
	conversation and populates session_to_conversation_id."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	session_id = "s-auto-create-001"
	assert session_id not in registry.session_to_conversation_id

	result = await handlers.notify_human(
		"hello",
		"Claude",
		cli_session_id=session_id,
		cwd="c:/work/proj",
	)

	assert result == "ok"
	assert session_id in registry.session_to_conversation_id
	conv_id = registry.session_to_conversation_id[session_id]
	assert conv_id.startswith("conv-")
	# The backend received the write addressed to the new conversation_id.
	assert len(backend.channel_messages) == 1
	assert backend.channel_messages[0]["channel_id"] == conv_id


@pytest.mark.asyncio
async def test_notify_human_routes_to_existing_conversation(cfg, logger):
	"""When cli_session_id is already bound, notify_human writes to the bound conversation."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	session_id = "s-existing-002"
	existing_conv_id = "conv-prebound-abc"
	registry.bind_session(session_id, existing_conv_id)

	result = await handlers.notify_human(
		"status update",
		"Claude",
		cli_session_id=session_id,
		cwd="c:/work/proj",
	)

	assert result == "ok"
	assert len(backend.channel_messages) == 1
	assert backend.channel_messages[0]["channel_id"] == existing_conv_id


@pytest.mark.asyncio
async def test_ask_human_auto_creates_conversation_on_first_call(cfg, logger):
	"""When cli_session_id is not yet bound, ask_human auto-creates a conversation,
	registers a pending request keyed to the new conv_id, and resolves correctly."""
	backend = RecordingBackend()
	registry = Registry()
	# Away mode ON so ask_human blocks (away-OFF path is the at-desk redirect).
	registry.global_away_mode = True
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	session_id = "s-ask-auto-003"
	assert session_id not in registry.session_to_conversation_id

	task = asyncio.create_task(
		handlers.ask_human(
			"Proceed?",
			"Claude",
			cli_session_id=session_id,
			cwd="c:/work/proj",
		)
	)
	# Let the handler reach the wait_for and register its pending entry.
	await asyncio.sleep(0)
	await asyncio.sleep(0)

	conv_id = registry.session_to_conversation_id.get(session_id)
	assert conv_id is not None
	assert conv_id.startswith("conv-")

	# Pending request must be keyed under (conv_id, "Claude").
	assert registry.pending_count == 1

	# Resolve via the registry.
	req_id = registry.resolve(conversation_id=conv_id, sender="Claude", text="yes")
	assert req_id is not None

	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"


@pytest.mark.asyncio
async def test_send_document_human_auto_creates_conversation(cfg, logger, tmp_path):
	"""When cli_session_id is not yet bound, send_document_human auto-creates a
	conversation and writes the document to that conversation_id."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	f = tmp_path / "report.txt"
	f.write_text("hello world")

	session_id = "s-doc-auto-004"
	assert session_id not in registry.session_to_conversation_id

	result = await handlers.send_document_human(
		"report.txt",
		"Claude",
		caption="Here it is",
		cli_session_id=session_id,
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)

	assert result == "ok"
	conv_id = registry.session_to_conversation_id.get(session_id)
	assert conv_id is not None
	assert conv_id.startswith("conv-")
	assert len(backend.sent_documents) == 1
	channel_id, content, url = backend.sent_documents[0]
	assert channel_id == conv_id
	assert content == "Here it is"


@pytest.mark.asyncio
async def test_missing_cli_session_id_returns_error(cfg, logger):
	"""Calling notify_human without cli_session_id returns the decorator's error."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Call without cli_session_id — the decorator should reject it.
	result = await handlers.notify_human(
		"hello",
		"Claude",
	)

	assert result.startswith("ERROR: cli_session_id required")
	assert len(backend.channel_messages) == 0
