"""T-150 (b) / F-67: set_away_mode(False) with pendings present resolves the
pendings, then if the Firebase persist of the flag raises, the handler returns
the ERROR string (registry/phone split-brain is surfaced, not swallowed)."""
import asyncio
import pytest

from server.gateway import build_tool_handlers
from tests.test_gateway_notify_human import RecordingBackend
from tests.conftest import make_registry_with_loopback

_CWD = "c:/work/sw"
_SENDER = "Claude"
_SID = "s-awayfalse-001"


class _PersistFailBackend(RecordingBackend):
	async def set_global_away_mode(self, value):
		raise RuntimeError("firebase down")


@pytest.fixture
def cfg(tmp_path):
	from server.config import Config
	return Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
	)


@pytest.fixture
def logger(cfg):
	from server.logging_jsonl import JsonlLogger
	return JsonlLogger(cfg.log_path)


@pytest.mark.asyncio
async def test_set_away_mode_false_with_pendings_persist_failure_returns_error(cfg, logger):
	registry = make_registry_with_loopback()  # global_away_mode=True
	backend = _PersistFailBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# A pending ask_human is in flight.
	t = asyncio.create_task(handlers.ask_human("Q", _SENDER, cli_session_id=_SID, cwd=_CWD))
	for _ in range(3):
		await asyncio.sleep(0)
	assert registry.pending_count == 1

	result = await handlers.set_away_mode(False, cli_session_id="s-x", cwd=_CWD)

	assert result.startswith("ERROR:"), f"expected ERROR on persist failure, got {result!r}"
	assert registry.global_away_mode is False, "in-memory flag must still flip"
	# The pending was resolved with the at-desk notice before the persist attempt.
	assert registry.pending_count == 0

	try:
		await t
	except (asyncio.CancelledError, Exception):
		pass
