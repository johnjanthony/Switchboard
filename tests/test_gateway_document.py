"""Tests for _validate_path security boundary and send_document_human handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import _validate_path, build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.registry import Registry
from tests.test_gateway_notify_human import RecordingBackend


# ── _validate_path ────────────────────────────────────────────────────────────


def test_validate_path_accepts_file_in_cwd(tmp_path):
	f = tmp_path / "report.txt"
	f.write_text("hello")
	resolved = _validate_path("report.txt", cwd=tmp_path)
	assert resolved == f.resolve()


def test_validate_path_accepts_file_in_subdirectory(tmp_path):
	sub = tmp_path / "sub"
	sub.mkdir()
	f = sub / "report.txt"
	f.write_text("hello")
	resolved = _validate_path("sub/report.txt", cwd=tmp_path)
	assert resolved == f.resolve()


def test_validate_path_accepts_absolute_path(tmp_path):
	f = tmp_path / "report.txt"
	f.write_text("hello")
	resolved = _validate_path(str(f), cwd=tmp_path)
	assert resolved == f.resolve()


def test_validate_path_rejects_absolute_path_denylist(tmp_path):
	f = tmp_path / ".env"
	f.write_text("SECRET=very_secret")
	with pytest.raises(ValueError, match="deny list"):
		_validate_path(str(f), cwd=tmp_path)


def test_validate_path_rejects_parent_traversal(tmp_path):
	sibling = tmp_path.parent / "secret.txt"
	sibling.write_text("top secret")
	with pytest.raises(ValueError, match="escapes"):
		_validate_path("../secret.txt", cwd=tmp_path)


def test_validate_path_rejects_nonexistent_file(tmp_path):
	with pytest.raises(ValueError, match="not found"):
		_validate_path("missing.txt", cwd=tmp_path)


def test_validate_path_rejects_directory(tmp_path):
	sub = tmp_path / "subdir"
	sub.mkdir()
	with pytest.raises(ValueError, match="Not a file"):
		_validate_path("subdir", cwd=tmp_path)


def test_validate_path_rejects_oversized_file(tmp_path):
	f = tmp_path / "big.bin"
	f.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
	with pytest.raises(ValueError, match="too large"):
		_validate_path("big.bin", cwd=tmp_path)


@pytest.mark.parametrize("name", [".env", "service-account.json"])
def test_validate_path_rejects_denylist_exact(tmp_path, name):
	f = tmp_path / name
	f.write_text("secret")
	with pytest.raises(ValueError, match="deny list"):
		_validate_path(name, cwd=tmp_path)


@pytest.mark.parametrize("name", [
	"api_token.json",
	"my.secret",
	"cert.pem",
	"private.key",
	".env.local",
	".env.production",
	".envrc",
	"prod.env",
	"staging.env",
])
def test_validate_path_rejects_denylist_glob(tmp_path, name):
	f = tmp_path / name
	f.write_text("secret")
	with pytest.raises(ValueError, match="restricted pattern"):
		_validate_path(name, cwd=tmp_path)


# ── send_document_human handler ───────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path):
	return Config(
		telegram_bot_token="tok",
		telegram_chat_id="123",
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_send_document_human_success(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	f = tmp_path / "report.txt"
	f.write_text("hello world")

	result = await handlers.send_document_human(
		"report.txt", "IR2", "Here's the report", cwd=tmp_path
	)

	assert result == "ok"
	assert len(backend.sent_documents) == 1
	agent_id, sent_path, sent_caption = backend.sent_documents[0]
	assert agent_id == "IR2"
	assert Path(sent_path) == f.resolve()
	assert sent_caption == "Here's the report"

	# Audit log must record the delivery.
	log_text = (tmp_path / "log.jsonl").read_text()
	events = [json.loads(line) for line in log_text.splitlines() if line]
	doc_events = [ev for ev in events if ev.get("event") == "document_sent"]
	assert len(doc_events) == 1
	ev = doc_events[0]
	assert ev["agent_id"] == "IR2"
	assert ev["size_bytes"] == len(b"hello world")
	assert len(ev["sha256"]) == 64  # SHA-256 hex is always 64 chars
	assert ev["caption_preview"] == "Here's the report"

	# Session log must record the delivery.
	sessions_dir = tmp_path / "sessions"
	session_files = list(sessions_dir.glob("IR2_*.log"))
	assert len(session_files) == 1
	session_text = session_files[0].read_text()
	assert "[document: report.txt]" in session_text
	assert "Here's the report" in session_text


@pytest.mark.asyncio
async def test_send_document_human_no_caption(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	f = tmp_path / "report.txt"
	f.write_text("hello")

	result = await handlers.send_document_human("report.txt", "IR2", cwd=tmp_path)

	assert result == "ok"
	assert backend.sent_documents[0][2] is None


@pytest.mark.asyncio
async def test_send_document_human_path_error_returns_error_string(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.send_document_human(
		"nonexistent.txt", "IR2", cwd=tmp_path
	)

	assert result.startswith("ERROR:")
	assert backend.sent_documents == []


@pytest.mark.asyncio
async def test_send_document_human_denylist_returns_error_string(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	f = tmp_path / ".env"
	f.write_text("SECRET=very_secret")

	result = await handlers.send_document_human(".env", "IR2", cwd=tmp_path)

	assert result.startswith("ERROR:")
	assert backend.sent_documents == []


@pytest.mark.asyncio
async def test_send_document_human_backend_error_returns_error_string(cfg, logger, tmp_path):
	class BrokenDocBackend(RecordingBackend):
		async def send_document(self, agent_id, path, caption=None):
			raise RuntimeError("telegram boom")

	backend = BrokenDocBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	f = tmp_path / "report.txt"
	f.write_text("hello")

	result = await handlers.send_document_human("report.txt", "IR2", cwd=tmp_path)

	assert result.startswith("ERROR:")
	assert "telegram boom" in result
