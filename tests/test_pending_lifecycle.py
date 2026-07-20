"""Unit tests for terminate_pending, the single terminal-path owner (DT-1)."""
import asyncio

from server.registry import Registry
from server.gateway.pending_lifecycle import terminate_pending


class FakeBackend:
	def __init__(self, fail=False):
		self.cancelled = []
		self.fail = fail

	async def mark_question_cancelled(self, conversation_id, request_id):
		if self.fail:
			raise RuntimeError("boom")
		self.cancelled.append((conversation_id, request_id))


class FakeLogger:
	def __init__(self):
		self.errors = []

	async def surface_error(self, msg, **kw):
		self.errors.append(msg)


def test_cancels_live_future_and_marks_firebase():
	async def run():
		r = Registry()
		backend, logger = FakeBackend(), FakeLogger()
		fut = r.add("conv-1", "sess-A", "Claude", "req-1")
		rec = r.find_by_request_id("conv-1", "req-1")
		assert await terminate_pending(r, backend, logger, rec) is True
		assert fut.cancelled()
		assert r.pending_count == 0
		assert backend.cancelled == [("conv-1", "req-1")]
		assert not r.was_recently_resolved("conv-1", "req-1")  # default: no replay memory
	asyncio.run(run())


def test_resolves_live_future_with_terminal_sentinel():
	async def run():
		r = Registry()
		backend, logger = FakeBackend(), FakeLogger()
		fut = r.add("conv-1", "sess-A", "Claude", "req-1")
		rec = r.find_by_request_id("conv-1", "req-1")
		sentinel = "__CONVERSATION_ENDED__\n(force-ended)"
		await terminate_pending(r, backend, logger, rec, resolve_text=sentinel, remember_resolved=True)
		assert fut.result() == sentinel
		assert r.was_recently_resolved("conv-1", "req-1")
		assert backend.cancelled == [("conv-1", "req-1")]
	asyncio.run(run())


def test_parked_record_terminates_without_future():
	async def run():
		r = Registry()
		backend, logger = FakeBackend(), FakeLogger()
		r.add_parked("conv-1", "sess-A", "Claude", "req-1")
		rec = r.find_by_request_id("conv-1", "req-1")
		assert await terminate_pending(r, backend, logger, rec, remember_resolved=True) is True
		assert r.pending_count == 0
		assert backend.cancelled == [("conv-1", "req-1")]
		assert r.was_recently_resolved("conv-1", "req-1")
	asyncio.run(run())


def test_superseded_record_is_left_alone():
	async def run():
		r = Registry()
		backend, logger = FakeBackend(), FakeLogger()
		r.add("conv-1", "sess-A", "Claude", "req-1")
		old = r.find_by_request_id("conv-1", "req-1")
		r.add("conv-1", "sess-A", "Claude", "req-2")
		assert await terminate_pending(r, backend, logger, old) is False
		assert backend.cancelled == []  # no side effects on a lost identity check
		assert r.find_by_request_id("conv-1", "req-2") is not None
	asyncio.run(run())


def test_mark_cancelled_false_skips_firebase():
	async def run():
		r = Registry()
		backend, logger = FakeBackend(), FakeLogger()
		r.add("conv-1", "sess-A", "Claude", "req-1")
		rec = r.find_by_request_id("conv-1", "req-1")
		assert await terminate_pending(r, backend, logger, rec, mark_cancelled=False) is True
		assert backend.cancelled == []
	asyncio.run(run())


def test_mark_cancelled_failure_is_surfaced_not_raised():
	async def run():
		r = Registry()
		backend, logger = FakeBackend(fail=True), FakeLogger()
		r.add_parked("conv-1", "sess-A", "Claude", "req-1")
		rec = r.find_by_request_id("conv-1", "req-1")
		assert await terminate_pending(r, backend, logger, rec) is True
		assert logger.errors and "terminate_pending_mark_cancelled_failed" in logger.errors[0]
	asyncio.run(run())


def test_backend_and_logger_may_be_none():
	async def run():
		r = Registry()
		r.add_parked("conv-1", "sess-A", "Claude", "req-1")
		rec = r.find_by_request_id("conv-1", "req-1")
		assert await terminate_pending(r, None, None, rec) is True
	asyncio.run(run())
