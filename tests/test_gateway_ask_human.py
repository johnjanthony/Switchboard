"""Happy-path test for the ask_human tool handler."""

import asyncio

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
async def test_ask_human_returns_response_when_resolved(cfg, logger):
	backend = RecordingBackend()
	registry = Registry()
	registry.set_away_mode(True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("Overwrite foo?", "ir2-chan-001"))
	await asyncio.sleep(0)
	resolved = registry.resolve_by_correlation(1000, "yes")
	assert resolved is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"
	assert len(backend.sent_questions) == 1
	assert len(backend.sent_confirmations) == 1
	_, _, correlation, response_text = backend.sent_confirmations[0]
	assert correlation == 1000
	assert response_text == "yes"


from server.gateway import dispatch_responses
from server.messenger import IncomingResponse


class YieldingBackend(RecordingBackend):
	def __init__(self, responses):
		super().__init__()
		self._responses = list(responses)

	async def poll_responses(self):
		for r in self._responses:
			yield r
		await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_dispatch_loop_routes_responses_to_registry(cfg, logger):
	registry = Registry()
	registry.set_away_mode(True)
	backend = YieldingBackend([IncomingResponse(correlation=1000, text="yes")])
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	ask_task = asyncio.create_task(handlers.ask_human("q", "ir2-chan-001"))
	await asyncio.sleep(0)  # let ask_human register
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)

	try:
		result = await asyncio.wait_for(ask_task, timeout=1.0)
		assert result == "yes"
	finally:
		dispatch_task.cancel()
		try:
			await dispatch_task
		except asyncio.CancelledError:
			pass


@pytest.mark.asyncio
async def test_dispatch_loop_logs_unknown_correlation(cfg, logger, tmp_path):
	registry = Registry()
	backend = YieldingBackend(
		[IncomingResponse(correlation=9999, text="stray")]
	)
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)
	# Give it a moment to consume the stray response.
	await asyncio.sleep(0.05)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	log_text = (tmp_path / "log.jsonl").read_text()
	assert "surface_error" in log_text
	assert "9999" in log_text


class RaisingBackend(RecordingBackend):
	async def poll_responses(self):
		yield IncomingResponse(correlation=1000, text="first")
		yield IncomingResponse(correlation=1000, text="second")
		# Hang.
		await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_dispatch_loop_continues_after_iteration_exception(
	cfg, logger, tmp_path, monkeypatch
):
	"""If an iteration raises unexpectedly, the loop logs surface_error and keeps running."""
	registry = Registry()
	backend = RaisingBackend()

	call_count = {"n": 0}
	original_resolve = registry.resolve_by_correlation

	def flaky_resolve(correlation, text):
		call_count["n"] += 1
		if call_count["n"] == 1:
			raise RuntimeError("kaboom")
		return original_resolve(correlation, text)

	monkeypatch.setattr(registry, "resolve_by_correlation", flaky_resolve)

	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)
	# Let both yielded responses be consumed.
	await asyncio.sleep(0.1)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	log_text = (tmp_path / "log.jsonl").read_text()
	assert "kaboom" in log_text or "surface_error" in log_text
	# The loop saw two responses; first raised, second was processed by the
	# real resolve (no pending request, so surface_error for unknown).
	assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_ask_human_cleans_up_registry_on_cancellation(cfg, logger):
	"""If the ask_human coroutine is cancelled mid-wait, the registry
	entry must not be left behind."""
	backend = RecordingBackend()
	registry = Registry()
	registry.set_away_mode(True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("q", "IR2"))
	await asyncio.sleep(0)  # let it register
	# Confirm the request registered.
	assert registry.resolve_by_correlation(1000, "placeholder") is not None
	# That resolution just popped the entry, so re-add for the real test:
	task = asyncio.create_task(handlers.ask_human("q2", "IR2"))
	await asyncio.sleep(0)
	# Now cancel mid-wait.
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
	# Registry must be clean — correlation 1001 was used for the second call.
	assert registry.resolve_by_correlation(1001, "late") is None


class FirstCallCrashesBackend(RecordingBackend):
	def __init__(self):
		super().__init__()
		self._calls = 0

	async def poll_responses(self):
		self._calls += 1
		if self._calls == 1:
			raise RuntimeError("async-for blowup")
		# Second call: yield nothing, then hang.
		await asyncio.Event().wait()
		if False:
			yield  # pragma: no cover


@pytest.mark.asyncio
async def test_dispatch_loop_restarts_after_iterator_crash(cfg, logger, tmp_path):
	registry = Registry()
	backend = FirstCallCrashesBackend()
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)
	# Give the outer loop time to crash, log, sleep 1s, and re-enter.
	await asyncio.sleep(1.3)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass
	assert backend._calls >= 2
	log_text = (tmp_path / "log.jsonl").read_text()
	assert "dispatch_loop_crashed" in log_text


@pytest.mark.asyncio
async def test_concurrent_ask_human_calls_resolve_independently(cfg, logger):
	"""Two concurrent ask_human calls, resolved out of order via the
	dispatch loop, each return their own reply."""
	registry = Registry()
	registry.set_away_mode(True)
	backend = YieldingBackend([
		IncomingResponse(correlation=1001, text="answer-to-second"),
		IncomingResponse(correlation=1000, text="answer-to-first"),
	])
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	first = asyncio.create_task(handlers.ask_human("q1", "chan-a"))
	await asyncio.sleep(0)
	second = asyncio.create_task(handlers.ask_human("q2", "chan-b"))
	await asyncio.sleep(0)

	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger)
	)

	try:
		r1, r2 = await asyncio.wait_for(
			asyncio.gather(first, second), timeout=1.0
		)
		assert r1 == "answer-to-first"
		assert r2 == "answer-to-second"
	finally:
		dispatch_task.cancel()
		try:
			await dispatch_task
		except asyncio.CancelledError:
			pass


@pytest.mark.asyncio
async def test_ask_human_at_desk_returns_redirect_and_delivers_as_notify(cfg, logger):
	"""When away mode is off, ask_human delivers a passive notify and returns the
	at-desk redirect error so the agent produces the question in the terminal."""
	backend = RecordingBackend()
	registry = Registry()
	# away_mode_active defaults to False — no set_away_mode call needed.
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human(
		"Should I overwrite foo.java?", "chan-atdesk-001", suggestions=["yes", "no"]
	)

	# Returns the redirect sentinel.
	assert result == "ERROR: John is at his desk. Ask this question via the terminal."

	# Backend received exactly one write — a notify, not a question.
	assert len(backend.channel_messages) == 1
	msg = backend.channel_messages[0]
	assert msg["message_type"] == "notify"
	assert msg["content"] == "Should I overwrite foo.java?"
	# No request_id and no suggestions on the downgraded notify.
	assert msg["request_id"] is None
	assert msg["suggestions"] is None

	# No pending request registered.
	assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_ask_human_away_mode_blocks_as_before(cfg, logger):
	"""When away mode is active, ask_human registers a pending request and blocks
	until a response arrives — existing behavior is preserved."""
	backend = RecordingBackend()
	registry = Registry()
	registry.set_away_mode(True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("Proceed with migration?", "chan-away-001"))
	await asyncio.sleep(0)

	# A question was written to the backend.
	assert len(backend.sent_questions) == 1
	# A pending request was registered.
	assert registry.pending_count == 1

	# Resolve via the recorded correlation token.
	resolved = registry.resolve_by_correlation(1000, "yes")
	assert resolved is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"
