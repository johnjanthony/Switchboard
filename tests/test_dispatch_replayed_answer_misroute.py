"""T-148 manifestation (a) ANSWER MISROUTE: a replayed answer for a superseded
request_id (e.g. from the _on_answer reconnect snapshot, before the slot delete
lands) must NOT resolve the newer entry now holding the (conversation_id, sender)
key. Reproduced against the real dispatch_responses loop."""
import asyncio
import pytest

from server.config import Config
from server.gateway import dispatch_responses
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse
from tests.conftest import make_registry_with_loopback, _make_loop_supervisor


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

_CONV = "conv-misroute"
_SENDER = "Claude"


class _GatedBackend:
	"""Yields the Q1 answer, waits until the test re-asks as Q2, then replays
	the Q1 answer (simulating the _on_answer reconnect snapshot re-enqueue)."""

	def __init__(self, gate: asyncio.Event):
		self._gate = gate
		self.deleted_slots = []

	async def poll_responses(self):
		yield IncomingResponse(correlation=(_CONV, _SENDER), text="answer-1", slot=f"{_CONV}/answers/req-1", request_id="req-1")
		await self._gate.wait()
		# Reconnect snapshot replay of the SAME (still-present) Q1 answer.
		yield IncomingResponse(correlation=(_CONV, _SENDER), text="answer-1", slot=f"{_CONV}/answers/req-1", request_id="req-1")
		await asyncio.Event().wait()

	async def delete_response_slot(self, slot):
		self.deleted_slots.append(slot)

	async def send_stale_reply_notice(self, conversation_id, sender):
		pass

	async def write_conversation_message(self, *a, **k):
		return ""


@pytest.mark.asyncio
async def test_replayed_answer_does_not_misroute_to_superseded_entry(cfg, logger):
	registry = make_registry_with_loopback()
	gate = asyncio.Event()
	backend = _GatedBackend(gate)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	# Q1 is pending.
	fut1 = registry.add(conversation_id=_CONV, sender=_SENDER, request_id="req-1")

	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup))
	# Let the first (legitimate) answer resolve Q1.
	for _ in range(5):
		await asyncio.sleep(0)
	assert fut1.result() == "answer-1"

	# The asker re-asks: a NEW pending entry (req-2) takes the same key.
	fut2 = registry.add(conversation_id=_CONV, sender=_SENDER, request_id="req-2")

	# Now release the replayed Q1 answer.
	gate.set()
	for _ in range(5):
		await asyncio.sleep(0)

	assert not fut2.done(), (
		"MISROUTE: the replayed Q1 answer resolved the superseded Q2 entry"
	)
	assert registry.pending_count == 1, "Q2 must remain pending"

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
