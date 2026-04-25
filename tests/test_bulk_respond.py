"""Tests for Slice I: bulk-respond on global exit."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Registry


def make_config(tmp_path: Path) -> Config:
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


def make_backend(message_texts: dict[tuple[str, str], str] | None = None) -> MagicMock:
	"""Build a minimal backend mock for bulk-respond tests.
	message_texts maps (cwd, msg_id) -> text."""
	backend = MagicMock()
	backend.send_resolution_confirmation = AsyncMock()

	async def _fetch_message_text(cwd: str, msg_id: str) -> str | None:
		return (message_texts or {}).get((cwd, msg_id))

	backend.fetch_message_text = _fetch_message_text
	return backend


def _add_pending(registry: Registry, cwd: str, sender: str, request_id: str, msg_id: str | None = None) -> asyncio.Future:
	return registry.add(cwd=cwd, sender=sender, request_id=request_id, msg_id=msg_id)


@pytest.mark.asyncio
async def test_build_payload_groups_by_cwd_sorted(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	_add_pending(registry, "c:/work/beta", "Claude", "req-b1")
	_add_pending(registry, "c:/work/alpha", "Gemini", "req-a1")
	_add_pending(registry, "c:/work/beta", "Gemini", "req-b2")

	payload = await handlers.build_bulk_respond_payload()
	assert "sections" in payload
	cwds = [s["cwd"] for s in payload["sections"]]
	assert cwds == sorted(cwds)
	# alpha first
	assert cwds[0] == "c:/work/alpha"
	assert cwds[1] == "c:/work/beta"


@pytest.mark.asyncio
async def test_build_payload_entries_include_request_id_sender_question(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend(message_texts={("c:/work/foo", "msg-1"): "What do you want?"})
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	_add_pending(registry, "c:/work/foo", "Claude", "req-1", msg_id="msg-1")

	payload = await handlers.build_bulk_respond_payload()
	assert len(payload["sections"]) == 1
	entry = payload["sections"][0]["entries"][0]
	assert entry["request_id"] == "req-1"
	assert entry["sender"] == "Claude"
	assert entry["question_text"] == "What do you want?"


@pytest.mark.asyncio
async def test_build_payload_question_text_empty_when_no_msg_id(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	_add_pending(registry, "c:/work/foo", "Claude", "req-1", msg_id=None)

	payload = await handlers.build_bulk_respond_payload()
	entry = payload["sections"][0]["entries"][0]
	assert entry["question_text"] == ""


@pytest.mark.asyncio
async def test_build_payload_default_text_present(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	_add_pending(registry, "c:/work/foo", "Claude", "req-1")
	payload = await handlers.build_bulk_respond_payload()
	assert "default_text" in payload
	assert payload["default_text"]


@pytest.mark.asyncio
async def test_bulk_respond_send_to_all_resolves_each_pending(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	fut1 = _add_pending(registry, "c:/work/a", "Claude", "req-1")
	fut2 = _add_pending(registry, "c:/work/b", "Gemini", "req-2")

	await handlers.bulk_respond_send_to_all("Back at desk")

	assert fut1.done() and fut1.result() == "Back at desk"
	assert fut2.done() and fut2.result() == "Back at desk"
	assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_bulk_respond_skip_leaves_pending_intact(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	_add_pending(registry, "c:/work/a", "Claude", "req-1")
	_add_pending(registry, "c:/work/b", "Gemini", "req-2")

	await handlers.bulk_respond_skip()

	assert registry.pending_count == 2


@pytest.mark.asyncio
async def test_bulk_respond_cancel_re_sets_global_away(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))

	registry.set_global_away(False)
	assert registry.global_away() is False

	await handlers.bulk_respond_cancel()

	assert registry.global_away() is True


@pytest.mark.asyncio
async def test_bulk_respond_payload_empty_when_no_pending(tmp_path):
	from server.gateway import build_tool_handlers
	registry = Registry()
	backend = make_backend()
	cfg = make_config(tmp_path)
	handlers = build_tool_handlers(cfg, registry, backend, JsonlLogger(cfg.log_path))
	payload = await handlers.build_bulk_respond_payload()
	assert payload["sections"] == []
