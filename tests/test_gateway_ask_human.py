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
		telegram_bot_token="tok",
		telegram_chat_id="123",
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
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Fire ask_human as a task; resolve it after a moment.
	task = asyncio.create_task(handlers.ask_human("Overwrite foo?", "IR2"))
	# Give the handler a tick to register the pending request.
	await asyncio.sleep(0)
	# RecordingBackend assigns correlation 1000 to the first question.
	resolved = registry.resolve_by_correlation(1000, "yes")
	assert resolved is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"
	# Backend was asked to send the question and the confirmation.
	assert len(backend.sent_questions) == 1
	assert len(backend.sent_confirmations) == 1
	# Confirmation carries the same correlation.
	_, _, correlation = backend.sent_confirmations[0]
	assert correlation == 1000


from server.gateway import dispatch_responses
from server.messenger import IncomingResponse


class YieldingBackend(RecordingBackend):
	def __init__(self, responses):
		super().__init__()
		self._responses = list(responses)

	async def poll_responses(self):
		for r in self._responses:
			yield r
		# Then hang so the dispatch task does not exit on its own.
		await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_dispatch_loop_routes_responses_to_registry(cfg, logger):
	registry = Registry()
	backend = YieldingBackend([IncomingResponse(correlation=1000, text="yes")])
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	ask_task = asyncio.create_task(handlers.ask_human("q", "IR2"))
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
