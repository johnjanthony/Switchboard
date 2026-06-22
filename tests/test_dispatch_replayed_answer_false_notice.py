"""M3: a replayed answer for an ALREADY-DELIVERED request (e.g. the answers
listener's reconnect snapshot re-enqueues an answer whose fire-and-forget slot
delete had not yet committed) must NOT write John a false 'reply for X couldn't
be delivered — the question was withdrawn' notice. The reply WAS delivered; only
the orphan slot needs dropping. A genuinely unknown correlation still gets the
notice (existing behavior preserved). Reproduced against the real
dispatch_responses loop."""
import asyncio
import pytest

from server.config import Config
from server.gateway import dispatch_responses
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse
from tests.conftest import make_registry_with_loopback, _make_loop_supervisor

_CONV = "conv-m3"
_SENDER = "Claude"


async def _until(cond, cap: int = 200, dt: float = 0.005) -> bool:
	"""Pump the loop with real sleeps (so asyncio.to_thread logger writes can
	complete) until cond() is true or the cap is hit. Returns cond()'s final
	value."""
	for _ in range(cap):
		if cond():
			return True
		await asyncio.sleep(dt)
	return cond()


async def _settle(dt: float = 0.4):
	"""Generous real-time settle for asserting the ABSENCE of a side effect:
	long enough that the stale-notice path (which awaits a to_thread logger
	write before notifying) would have completed if it were taken."""
	await asyncio.sleep(dt)


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


class _ReplayBackend:
	"""Yield the real answer (which resolves the live pending), wait for the
	gate, then replay the SAME answer (the reconnect-snapshot re-enqueue)."""

	def __init__(self, gate: asyncio.Event):
		self._gate = gate
		self.deleted_slots: list[str] = []
		self.stale_notices: list[tuple] = []

	async def poll_responses(self):
		yield IncomingResponse(correlation=(_CONV, _SENDER), text="answer-1", slot=f"{_CONV}/answers/req-1", request_id="req-1")
		await self._gate.wait()
		yield IncomingResponse(correlation=(_CONV, _SENDER), text="answer-1", slot=f"{_CONV}/answers/req-1", request_id="req-1")
		await asyncio.Event().wait()

	async def delete_response_slot(self, slot):
		self.deleted_slots.append(slot)

	async def send_stale_reply_notice(self, conversation_id, sender):
		self.stale_notices.append((conversation_id, sender))

	async def write_conversation_message(self, *a, **k):
		return ""


class _UnknownBackend:
	"""Yields a single answer whose correlation was never pending."""

	def __init__(self):
		self.deleted_slots: list[str] = []
		self.stale_notices: list[tuple] = []

	async def poll_responses(self):
		yield IncomingResponse(correlation=(_CONV, "Ghost"), text="huh", slot=f"{_CONV}/answers/req-x", request_id="req-x")
		await asyncio.Event().wait()

	async def delete_response_slot(self, slot):
		self.deleted_slots.append(slot)

	async def send_stale_reply_notice(self, conversation_id, sender):
		self.stale_notices.append((conversation_id, sender))

	async def write_conversation_message(self, *a, **k):
		return ""


@pytest.mark.asyncio
async def test_replayed_delivered_answer_does_not_send_false_withdrawn_notice(cfg, logger):
	registry = make_registry_with_loopback()
	gate = asyncio.Event()
	backend = _ReplayBackend(gate)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	fut = registry.add(conversation_id=_CONV, sender=_SENDER, request_id="req-1")
	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup))
	await _until(lambda: fut.done())
	assert fut.result() == "answer-1"  # delivered

	# No re-ask. Release the replay of the same, already-delivered answer, then
	# wait until the replay has been processed (its slot delete is the last
	# await in both the stale and the suppressed branch, so once we see a second
	# delete the notice decision is already made), and settle for good measure.
	gate.set()
	await _until(lambda: backend.deleted_slots.count(f"{_CONV}/answers/req-1") >= 2)
	await _settle()

	assert backend.stale_notices == [], "a replayed delivered answer must not produce a 'withdrawn' notice"
	assert f"{_CONV}/answers/req-1" in backend.deleted_slots, "the orphan slot must still be dropped"

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_genuinely_unknown_answer_still_sends_stale_notice(cfg, logger):
	registry = make_registry_with_loopback()
	backend = _UnknownBackend()
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup))
	await _until(lambda: backend.stale_notices != [])

	assert backend.stale_notices == [(_CONV, "Ghost")], "an unknown correlation must still notify"
	assert f"{_CONV}/answers/req-x" in backend.deleted_slots

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
