"""_await_with_progress_keepalive: MCP progress pings while a blocking tool handler waits.

The keepalive exists because Claude Code >= 2.1.187 aborts a remote MCP tool
call that is silent (no response, no progress notification) for 5 minutes.
ask_human legitimately blocks for hours, so the server must heartbeat.
"""

import asyncio

import pytest

from server.main import _await_with_progress_keepalive


class _FakeContext:
	def __init__(self, raise_on_report: Exception | None = None):
		self.calls = []
		self._raise = raise_on_report

	async def report_progress(self, progress, total=None, message=None):
		if self._raise is not None:
			raise self._raise
		self.calls.append((progress, total, message))


class _FakeMCP:
	def __init__(self, ctx=None, raise_on_get_context: Exception | None = None):
		self.ctx = ctx if ctx is not None else _FakeContext()
		self._raise = raise_on_get_context

	def get_context(self):
		if self._raise is not None:
			raise self._raise
		return self.ctx


@pytest.mark.anyio
async def test_result_passes_through_without_pings_when_fast():
	mcp = _FakeMCP()

	async def fast_handler():
		return "answer"

	result = await _await_with_progress_keepalive(mcp, fast_handler(), interval=0.05)
	assert result == "answer"
	assert mcp.ctx.calls == []


@pytest.mark.anyio
async def test_pings_fire_while_handler_is_pending():
	mcp = _FakeMCP()
	release = asyncio.Event()

	async def slow_handler():
		await release.wait()
		return "late answer"

	waiter = asyncio.ensure_future(_await_with_progress_keepalive(mcp, slow_handler(), interval=0.02))
	await asyncio.sleep(0.09)
	release.set()
	result = await waiter
	assert result == "late answer"
	assert len(mcp.ctx.calls) >= 2
	beats = [c[0] for c in mcp.ctx.calls]
	assert beats == sorted(beats) and beats[0] == 1


@pytest.mark.anyio
async def test_handler_exception_propagates():
	mcp = _FakeMCP()

	async def failing_handler():
		raise ValueError("boom")

	with pytest.raises(ValueError, match="boom"):
		await _await_with_progress_keepalive(mcp, failing_handler(), interval=0.05)


@pytest.mark.anyio
async def test_cancellation_reaches_handler_cleanup():
	mcp = _FakeMCP()
	cleanup_ran = asyncio.Event()

	async def blocking_handler():
		try:
			await asyncio.Event().wait()
		except asyncio.CancelledError:
			cleanup_ran.set()
			raise

	waiter = asyncio.ensure_future(_await_with_progress_keepalive(mcp, blocking_handler(), interval=0.02))
	await asyncio.sleep(0.03)
	waiter.cancel()
	with pytest.raises(asyncio.CancelledError):
		await waiter
	assert cleanup_ran.is_set()


@pytest.mark.anyio
async def test_report_progress_failure_does_not_break_result():
	mcp = _FakeMCP(ctx=_FakeContext(raise_on_report=RuntimeError("stream gone")))
	release = asyncio.Event()

	async def slow_handler():
		await release.wait()
		return "still delivered"

	waiter = asyncio.ensure_future(_await_with_progress_keepalive(mcp, slow_handler(), interval=0.02))
	await asyncio.sleep(0.05)
	release.set()
	assert await waiter == "still delivered"


@pytest.mark.anyio
async def test_get_context_failure_falls_back_to_plain_await():
	mcp = _FakeMCP(raise_on_get_context=ValueError("no active request"))

	async def handler():
		return "works without keepalive"

	assert await _await_with_progress_keepalive(mcp, handler(), interval=0.05) == "works without keepalive"
