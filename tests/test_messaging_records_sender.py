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
async def test_notify_human_records_messaging_sender(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)

	await handlers.notify_human("hello", cwd, "Claude")

	from server.canonicalization import canonicalize_cwd
	canonical = canonicalize_cwd(cwd)
	assert registry.last_messaging_sender_for(canonical) == "Claude"


@pytest.mark.asyncio
async def test_send_document_human_records_messaging_sender(cfg, logger, tmp_path):
	registry = Registry()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)
	cwd = str(tmp_path)
	# Create a small file inside cwd to satisfy size/path checks
	(tmp_path / "note.txt").write_text("ok", encoding="utf-8")

	await handlers.send_document_human("note.txt", cwd, "Gemini")

	from server.canonicalization import canonicalize_cwd
	canonical = canonicalize_cwd(cwd)
	assert registry.last_messaging_sender_for(canonical) == "Gemini"
