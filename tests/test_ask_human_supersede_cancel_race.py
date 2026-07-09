"""T-148 manifestation (b) SUPERSEDE+CANCEL race: when Q1 is superseded by Q2
under the same (conv, cli_session_id) key, Q1's shielded cancellation cleanup
must remove only its OWN entry, never the live Q2 entry that superseded it.

REV-106: superseding Q1 resolves Q1's future with the superseded sentinel
(not a cancel), so Q1's coroutine completes normally with a terminal envelope
rather than raising CancelledError."""
import asyncio
import json
import pytest

from server.gateway import build_tool_handlers
from tests.test_gateway_notify_human import RecordingBackend
from tests.conftest import make_registry_with_loopback

from server.config import Config
from server.logging_jsonl import JsonlLogger

_CWD = "c:/work/sw"
_SENDER = "Claude"
_SID = "s-supersede-001"


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
async def test_superseded_asker_cleanup_does_not_remove_live_entry(cfg, logger):
	registry = make_registry_with_loopback()
	backend = RecordingBackend()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Q1 registers and blocks on its future.
	t1 = asyncio.create_task(handlers.ask_human("Q1", _SENDER, cli_session_id=_SID, cwd=_CWD))
	for _ in range(3):
		await asyncio.sleep(0)
	assert registry.pending_count == 1
	conv_id = registry.session_to_conversation_id[_SID]
	req1 = registry.pending_for_conversation(conv_id)[0].request_id

	# Q2 supersedes Q1 (installs req2 at the same key).
	t2 = asyncio.create_task(handlers.ask_human("Q2", _SENDER, cli_session_id=_SID, cwd=_CWD))

	# Q1 completes with the superseded envelope (REV-106): resolved, not
	# cancelled - a cancel would surface on Q1's MCP client as a transport
	# error it may retry.
	res1 = await asyncio.wait_for(t1, timeout=5.0)
	assert json.loads(res1) == {"status": "superseded"}

	# Q1's coroutine has now observed the resolved sentinel.
	remaining = registry.pending_for_conversation(conv_id)
	rec = remaining[0] if remaining else None
	assert rec is not None, "the live Q2 entry was wrongly removed by Q1's cleanup"
	assert rec.request_id != req1, "expected the live entry to be Q2 (req2), not Q1"
	assert registry.pending_count == 1, "Q2 must still be the single live pending"

	t2.cancel()
	try:
		await t2
	except asyncio.CancelledError:
		pass
