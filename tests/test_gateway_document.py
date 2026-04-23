"""Tests for _validate_path security boundary and send_document_human handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import _validate_path, build_tool_handlers
from server.logging_jsonl import JsonlLogger
from server.rate_limiter import RateLimiter
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
		"report.txt", "my-chan-001", caption="Here's the report", cwd=tmp_path
	)

	assert result == "ok"
	assert len(backend.sent_documents) == 1
	channel_id, sent_content, sent_url = backend.sent_documents[0]
	assert channel_id == "my-chan-001"
	assert Path(sent_url) == f.resolve()
	assert sent_content == "Here's the report"

	log_text = (tmp_path / "log.jsonl").read_text()
	events = [json.loads(line) for line in log_text.splitlines() if line]
	doc_events = [ev for ev in events if ev.get("event") == "document_sent"]
	assert len(doc_events) == 1
	ev = doc_events[0]
	assert ev["channel_id"] == "my-chan-001"
	assert ev["size_bytes"] == len(b"hello world")
	assert len(ev["sha256"]) == 64
	assert ev["caption_preview"] == "Here's the report"

	sessions_dir = tmp_path / "sessions"
	session_files = list(sessions_dir.glob("my-chan-001_*.log"))
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
	result = await handlers.send_document_human("report.txt", "my-chan-001", cwd=tmp_path)
	assert result == "ok"
	assert backend.sent_documents[0][1] == "report.txt"  # filename used as content


@pytest.mark.asyncio
async def test_send_document_human_path_error_returns_error_string(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.send_document_human("nonexistent.txt", "my-chan-001", cwd=tmp_path)
	assert result.startswith("ERROR:")
	assert backend.sent_documents == []


@pytest.mark.asyncio
async def test_send_document_human_denylist_returns_error_string(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	f = tmp_path / ".env"
	f.write_text("SECRET=very_secret")
	result = await handlers.send_document_human(".env", "my-chan-001", cwd=tmp_path)
	assert result.startswith("ERROR:")
	assert backend.sent_documents == []


@pytest.mark.asyncio
async def test_send_document_human_backend_error_returns_error_string(cfg, logger, tmp_path):
	class BrokenDocBackend(RecordingBackend):
		async def write_channel_message(self, channel_id, sender, message_type, content, **kwargs):
			if message_type == "document":
				raise RuntimeError("telegram boom")
			return await super().write_channel_message(channel_id, sender, message_type, content, **kwargs)

	backend = BrokenDocBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	f = tmp_path / "report.txt"
	f.write_text("hello")
	result = await handlers.send_document_human("report.txt", "my-chan-001", cwd=tmp_path)
	assert result.startswith("ERROR:")
	assert "telegram boom" in result


@pytest.mark.asyncio
async def test_send_document_human_returns_error_when_rate_limited(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	f = tmp_path / "report.txt"
	f.write_text("hello")
	limiter = RateLimiter(rate_per_minute=1)
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter)

	first = await handlers.send_document_human("report.txt", "chan-rl-001", cwd=tmp_path)
	result = await handlers.send_document_human("report.txt", "chan-rl-001", cwd=tmp_path)  # over limit

	assert first == "ok"
	assert result.startswith("ERROR: rate limit exceeded")
	assert "1 messages/min" in result
	assert "60 seconds" in result  # ceil(60/1) = 60
	assert len(backend.sent_documents) == 1  # second call did not reach backend


@pytest.mark.asyncio
async def test_send_document_human_path_error_bypasses_rate_limit(cfg, logger, tmp_path):
	"""Path validation errors are always returned — rate limit is checked after validation."""
	backend = RecordingBackend()
	registry = Registry()
	f = tmp_path / "report.txt"
	f.write_text("hello")
	limiter = RateLimiter(rate_per_minute=1)
	handlers = build_tool_handlers(cfg, registry, backend, logger, limiter)
	await handlers.send_document_human("report.txt", "chan-rl-002", cwd=tmp_path)  # exhausts the bucket
	# Channel is now rate-limited, but path error should still surface
	result = await handlers.send_document_human("nonexistent.txt", "chan-rl-002", cwd=tmp_path)
	assert result.startswith("ERROR:")
	assert "not found" in result
