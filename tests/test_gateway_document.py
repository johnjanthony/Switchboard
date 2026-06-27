"""Tests for _validate_path security boundary and send_document_human handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Config
from server.gateway import build_tool_handlers
from server.gateway.document import _validate_path
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
		"report.txt",
		"Claude",
		caption="Here's the report",
		cli_session_id="s-doc-success-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)

	assert result == "ok"
	assert len(backend.sent_documents) == 1
	channel_id, sent_content, sent_url = backend.sent_documents[0]
	# channel_id is the auto-created conversation_id
	assert channel_id.startswith("conv-")
	assert Path(sent_url) == f.resolve()
	assert sent_content == "Here's the report"

	log_text = (tmp_path / "log.jsonl").read_text()
	events = [json.loads(line) for line in log_text.splitlines() if line]
	doc_events = [ev for ev in events if ev.get("event") == "document_sent"]
	assert len(doc_events) == 1
	ev = doc_events[0]
	assert ev["size_bytes"] == len(b"hello world")
	assert len(ev["sha256"]) == 64
	assert ev["caption_preview"] == "Here's the report"


@pytest.mark.asyncio
async def test_send_document_human_no_caption(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	f = tmp_path / "report.txt"
	f.write_text("hello")
	result = await handlers.send_document_human(
		"report.txt",
		"Claude",
		cli_session_id="s-doc-nocap-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)
	assert result == "ok"
	assert backend.sent_documents[0][1] == "report.txt"  # filename used as content


@pytest.mark.asyncio
async def test_send_document_human_path_error_returns_error_string(cfg, logger, tmp_path):
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	result = await handlers.send_document_human(
		"nonexistent.txt",
		"Claude",
		cli_session_id="s-doc-patherr-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
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
	result = await handlers.send_document_human(
		".env",
		"Claude",
		cli_session_id="s-doc-deny-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)
	assert result.startswith("ERROR:")
	assert backend.sent_documents == []


@pytest.mark.asyncio
async def test_send_document_human_backend_error_returns_error_string(cfg, logger, tmp_path):
	class BrokenDocBackend(RecordingBackend):
		async def write_conversation_message(self, conv_id, sender_or_message, message_type=None, text=None, **kwargs):
			mt = message_type if not isinstance(sender_or_message, dict) else sender_or_message.get("type", "")
			if mt == "document":
				raise RuntimeError("telegram boom")
			return await super().write_conversation_message(conv_id, sender_or_message, message_type, text, **kwargs)

	backend = BrokenDocBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	f = tmp_path / "report.txt"
	f.write_text("hello")
	result = await handlers.send_document_human(
		"report.txt",
		"Claude",
		cli_session_id="s-doc-bknerr-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)
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

	first = await handlers.send_document_human(
		"report.txt",
		"Claude",
		cli_session_id="s-doc-rl-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)
	result = await handlers.send_document_human(
		"report.txt",
		"Claude",
		cli_session_id="s-doc-rl-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)  # over limit — same session → same conversation bucket

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
	await handlers.send_document_human(
		"report.txt",
		"Claude",
		cli_session_id="s-doc-rl-002",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)  # exhausts the bucket
	# Conversation is now rate-limited, but path error should still surface before the limit check.
	result = await handlers.send_document_human(
		"nonexistent.txt",
		"Claude",
		cli_session_id="s-doc-rl-002",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)
	assert result.startswith("ERROR:")
	assert "not found" in result



@pytest.mark.asyncio
async def test_send_document_human_title_passthrough(cfg, logger, tmp_path):
	"""title kwarg is forwarded to backend.write_conversation_message."""
	backend = RecordingBackend()
	registry = Registry()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	f = tmp_path / "report.txt"
	f.write_text("hello")
	result = await handlers.send_document_human(
		"report.txt",
		"Claude",
		title="My Session",
		cli_session_id="s-doc-title-001",
		cwd=str(tmp_path),
		_cwd_path=tmp_path,
	)
	assert result == "ok"
	# channel_messages[0] is the write_conversation_meta call's channel message;
	# channel_messages[-1] is the document write — find it by message_type.
	doc_msg = next(m for m in backend.channel_messages if m["message_type"] == "document")
	assert doc_msg["title"] == "My Session"


# ── content-type + blob-path helpers ──────────────────────────────────────────

from server.gateway.document import guess_content_type, _blob_path_from_url


def test_guess_content_type_markdown():
	assert guess_content_type("report.md") == "text/markdown"
	assert guess_content_type("a.markdown") == "text/markdown"


def test_guess_content_type_known_and_unknown():
	assert guess_content_type("data.json") == "application/json"
	assert guess_content_type("pic.png") == "image/png"
	assert guess_content_type("doc.pdf") == "application/pdf"
	assert guess_content_type("mystery.xyzzy") == "application/octet-stream"
	assert guess_content_type("server.log") == "text/plain"


def test_blob_path_from_signed_url():
	url = "https://storage.googleapis.com/my-bucket/documents/abc123/report.md?X-Goog-Signature=deadbeef"
	assert _blob_path_from_url(url) == "documents/abc123/report.md"


def test_blob_path_from_url_handles_encoded_and_missing():
	assert _blob_path_from_url("https://storage.googleapis.com/b/documents/x/a%20b.md?sig=1") == "documents/x/a b.md"
	assert _blob_path_from_url(None) is None
	assert _blob_path_from_url("https://example.com/not-a-doc/file.md") is None
