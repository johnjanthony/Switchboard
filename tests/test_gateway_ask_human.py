"""Happy-path and edge-case tests for the ask_human tool handler."""

import asyncio

import anyio
import pytest

from server.config import Config
from server.gateway import build_tool_handlers, dispatch_responses
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse
from server.registry import Registry
from tests.conftest import make_registry_with_loopback, _make_loop_supervisor
from tests.test_gateway_notify_human import RecordingBackend

_CWD = "c:/work/sw"
_SENDER = "Claude"


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


class CancelTrackingBackend(RecordingBackend):
	"""RecordingBackend that also captures mark_question_cancelled invocations,
	used to verify server-side handling of asyncio task cancellation."""

	def __init__(self) -> None:
		super().__init__()
		self.cancelled_questions: list[tuple[str, str]] = []

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		self.cancelled_questions.append((cwd, request_id))


class AwaitingCancelTrackingBackend(RecordingBackend):
	"""Like CancelTrackingBackend but `mark_question_cancelled` has REAL awaits
	inside, mimicking FirebaseBackend's two `asyncio.to_thread` checkpoints.
	If our handler's CancelledError block doesn't shield the cleanup, those
	checkpoints re-raise CancelledError before the recording write completes."""

	def __init__(self) -> None:
		super().__init__()
		self.cancelled_questions: list[tuple[str, str]] = []

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		# Simulate the two-checkpoint Firebase write: a read followed by a write.
		await asyncio.sleep(0)
		await asyncio.sleep(0)
		self.cancelled_questions.append((cwd, request_id))


@pytest.mark.asyncio
async def test_ask_human_cleanup_completes_despite_cancellation_with_awaits(cfg, logger):
	"""When the cancel scope cancels our handler, the CancelledError block must
	complete its Firebase cleanup write even though that write contains await
	checkpoints. Without shielding, those checkpoints re-raise CancelledError
	and the question never gets marked cancelled — exactly the live bug we
	observed (cancel notification arrives, framework logs 'cancelled', but
	Firebase shows cancelled=false).
	"""
	backend = AwaitingCancelTrackingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	scope_holder: list[anyio.CancelScope] = []

	async def run_inside_inner_scope():
		async with anyio.create_task_group() as tg:
			ask_started = anyio.Event()

			async def runner():
				with anyio.CancelScope() as scope:
					scope_holder.append(scope)
					ask_started.set()
					await handlers.ask_human("Overwrite foo?", _CWD, _SENDER)

			async def canceller():
				await ask_started.wait()
				for _ in range(50):
					await anyio.sleep(0)
					if registry.get((_CWD, _SENDER)) is not None:
						break
				assert registry.get((_CWD, _SENDER)) is not None
				assert scope_holder, "runner did not capture its scope"
				scope_holder[0].cancel()

			tg.start_soon(runner)
			tg.start_soon(canceller)

	await run_inside_inner_scope()

	assert registry.get((_CWD, _SENDER)) is None
	assert len(backend.cancelled_questions) == 1, (
		f"Expected 1 cancelled-question write, got {len(backend.cancelled_questions)}. "
		f"Cleanup awaits were re-cancelled before completing the Firebase write."
	)


@pytest.mark.asyncio
async def test_ask_human_marks_question_cancelled_under_inner_cancel_scope(cfg, logger):
	"""Mirror RequestResponder's exact pattern: a CancelScope entered INSIDE
	a task (not the task group's scope), then `.cancel()` invoked from a
	sibling task in the same task group. This reproduces precisely how MCP's
	responder.cancel() reaches our handler in production. If this passes but
	live cancels still don't propagate, the bug isn't in the cancel mechanism
	at all."""
	backend = CancelTrackingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	scope_holder: list[anyio.CancelScope] = []

	async def run_inside_inner_scope():
		async with anyio.create_task_group() as tg:
			ask_started = anyio.Event()

			async def runner():
				with anyio.CancelScope() as scope:
					scope_holder.append(scope)
					ask_started.set()
					await handlers.ask_human("Overwrite foo?", _CWD, _SENDER)

			async def canceller():
				await ask_started.wait()
				# Yield until the registry has the pending entry.
				for _ in range(50):
					await anyio.sleep(0)
					if registry.get((_CWD, _SENDER)) is not None:
						break
				assert registry.get((_CWD, _SENDER)) is not None
				assert scope_holder, "runner did not capture its scope"
				scope_holder[0].cancel()

			tg.start_soon(runner)
			tg.start_soon(canceller)

	await run_inside_inner_scope()

	assert registry.get((_CWD, _SENDER)) is None
	assert len(backend.cancelled_questions) == 1, (
		f"Expected 1 cancelled-question write, got {len(backend.cancelled_questions)}. "
		f"Inner-scope cancellation is not reaching our handler's cleanup."
	)


@pytest.mark.asyncio
async def test_ask_human_marks_question_cancelled_under_anyio_scope(cfg, logger):
	"""Reproduces the live MCP cancel path: when the FastMCP framework cancels
	the asyncio task running ask_human via an anyio CancelScope (which is what
	`responder.cancel()` actually does), the scope stays in a cancelled state
	for every subsequent checkpoint inside it. Our cleanup `await
	_safe_mark_cancelled(...)` is itself a checkpoint; without shielding it
	would also raise CancelledError and the question would never get marked.

	This mirrors the production transport flow that `task.cancel()` does NOT
	mirror — direct task cancel only schedules one CancelledError; an anyio
	scope cancellation persists."""
	backend = CancelTrackingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	async def run_inside_scope():
		async with anyio.create_task_group() as tg:
			ask_started = anyio.Event()

			async def runner():
				ask_started.set()
				await handlers.ask_human("Overwrite foo?", _CWD, _SENDER)

			tg.start_soon(runner)
			await ask_started.wait()
			# Yield until the registry has the pending entry — handlers do a
			# few awaits before adding it.
			for _ in range(50):
				await anyio.sleep(0)
				if registry.get((_CWD, _SENDER)) is not None:
					break
			assert registry.get((_CWD, _SENDER)) is not None, (
				"ask_human did not register a pending entry — test setup is wrong"
			)
			tg.cancel_scope.cancel()

	await run_inside_scope()

	# Pending entry must be cleared.
	assert registry.get((_CWD, _SENDER)) is None
	# mark_question_cancelled must have been called for the in-flight request_id.
	# This is the assertion that fails today without shielding, because the
	# cleanup `await` re-raises CancelledError before the Firebase write.
	assert len(backend.cancelled_questions) == 1, (
		f"Expected 1 cancelled-question write, got {len(backend.cancelled_questions)}. "
		f"This is the bug: anyio scope cancellation re-raises on cleanup awaits."
	)


@pytest.mark.asyncio
async def test_ask_human_marks_question_cancelled_on_task_cancel(cfg, logger):
	"""Diagnostic: when the asyncio task running ask_human is cancelled before
	the future resolves, the CancelledError block must run mark_question_cancelled
	and registry.remove. This isolates the server-side cancellation handling
	from the MCP transport that delivers the cancel signal — if this test passes
	but the live system fails to mark questions cancelled, the bug is upstream
	(MCP framework / Claude Code transport), not in our handler code."""
	backend = CancelTrackingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("Overwrite foo?", _CWD, _SENDER))
	# Yield so the handler reaches the wait_for and registers a pending entry.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert registry.get((_CWD, _SENDER)) is not None, (
		"ask_human did not register a pending entry — test setup is wrong"
	)

	# Direct asyncio cancel — same signal MCP would deliver if it propagated
	# the per-tool-call cancel notification from the client.
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	# Pending entry must be cleared.
	assert registry.get((_CWD, _SENDER)) is None
	# mark_question_cancelled must have been called for the in-flight request_id.
	assert len(backend.cancelled_questions) == 1
	cwd, request_id = backend.cancelled_questions[0]
	assert cwd == _CWD
	# Request ID is generated server-side; we just assert it's a non-empty string.
	assert isinstance(request_id, str) and request_id


@pytest.mark.asyncio
async def test_ask_human_returns_response_when_resolved(cfg, logger):
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("Overwrite foo?", _CWD, _SENDER))
	await asyncio.sleep(0)
	req_id = registry.resolve(cwd=_CWD, sender=_SENDER, text="yes")
	assert req_id is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"
	assert len(backend.sent_questions) == 1
	assert len(backend.sent_confirmations) == 1
	_, _, correlation, response_text = backend.sent_confirmations[0]
	assert response_text == "yes"


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
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	# Dispatch uses tuple correlation (cwd, sender)
	backend = YieldingBackend([IncomingResponse(correlation=(_CWD, _SENDER), text="yes")])
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	ask_task = asyncio.create_task(handlers.ask_human("q", _CWD, _SENDER))
	await asyncio.sleep(0)  # let ask_human register
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
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
	registry = make_registry_with_loopback()
	backend = YieldingBackend(
		[IncomingResponse(correlation=("c:/unknown", "Ghost"), text="stray")]
	)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
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


@pytest.mark.asyncio
async def test_dispatch_loop_logs_legacy_correlation(cfg, logger, tmp_path):
	"""Non-tuple correlations are logged as legacy_correlation_dropped."""
	registry = make_registry_with_loopback()
	backend = YieldingBackend(
		[IncomingResponse(correlation=9999, text="stray")]
	)
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
	)
	await asyncio.sleep(0.05)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	log_text = (tmp_path / "log.jsonl").read_text()
	assert "surface_error" in log_text
	assert "legacy_correlation_dropped" in log_text


@pytest.mark.asyncio
async def test_dispatch_loop_continues_after_iteration_exception(
	cfg, logger, tmp_path, monkeypatch
):
	"""If an iteration raises unexpectedly, the loop logs surface_error and keeps running."""
	registry = make_registry_with_loopback()
	backend = YieldingBackend([
		IncomingResponse(correlation=(_CWD, _SENDER), text="first"),
		IncomingResponse(correlation=(_CWD, _SENDER), text="second"),
	])

	call_count = {"n": 0}
	original_resolve = registry.resolve

	def flaky_resolve(cwd, sender, text):
		call_count["n"] += 1
		if call_count["n"] == 1:
			raise RuntimeError("kaboom")
		return original_resolve(cwd=cwd, sender=sender, text=text)

	monkeypatch.setattr(registry, "resolve", flaky_resolve)

	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
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
	assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_ask_human_cleans_up_registry_on_cancellation(cfg, logger):
	"""If the ask_human coroutine is cancelled mid-wait, the registry
	entry must not be left behind."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("q", _CWD, _SENDER))
	await asyncio.sleep(0)  # let it register
	assert registry.pending_count == 1
	# Cancel mid-wait.
	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
	# Registry must be clean.
	assert registry.pending_count == 0


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
	registry = make_registry_with_loopback()
	backend = FirstCallCrashesBackend()
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
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
	assert "dispatch_responses_loop_crashed" in log_text


@pytest.mark.asyncio
async def test_concurrent_ask_human_calls_resolve_independently(cfg, logger):
	"""Two concurrent ask_human calls (different cwds), resolved out of order via the
	dispatch loop, each return their own reply."""
	_CWD_A = "c:/work/chan-a"
	_CWD_B = "c:/work/chan-b"
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD_A, True)
	registry.set_cwd_override(_CWD_B, True)
	backend = YieldingBackend([
		IncomingResponse(correlation=(_CWD_B, _SENDER), text="answer-to-second"),
		IncomingResponse(correlation=(_CWD_A, _SENDER), text="answer-to-first"),
	])
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	first = asyncio.create_task(handlers.ask_human("q1", _CWD_A, _SENDER))
	await asyncio.sleep(0)
	second = asyncio.create_task(handlers.ask_human("q2", _CWD_B, _SENDER))
	await asyncio.sleep(0)

	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
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
	"""When away mode is off for this cwd, ask_human delivers a passive notify and
	returns the at-desk redirect error so the agent produces the question in the terminal."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	# is_away_mode_active(_CWD) defaults to False — no override needed.
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human(
		"Should I overwrite foo.java?", _CWD, _SENDER, suggestions=["yes", "no"]
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
async def test_ask_human_per_cwd_at_desk_redirect(cfg, logger):
	"""Global away=True but cwd override=False → at-desk redirect for that cwd."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.update_global_away_cache(True)
	registry.set_cwd_override(_CWD, False)
	registry.update_cwd_override_cache(_CWD, False)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("question?", _CWD, _SENDER)

	assert result == "ERROR: John is at his desk. Ask this question via the terminal."
	assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_ask_human_away_mode_blocks_as_before(cfg, logger):
	"""When away mode is active for this cwd, ask_human registers a pending request
	and blocks until a response arrives — existing behavior is preserved."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("Proceed with migration?", _CWD, _SENDER))
	await asyncio.sleep(0)

	# A question was written to the backend.
	assert len(backend.sent_questions) == 1
	# A pending request was registered.
	assert registry.pending_count == 1

	# Resolve via (cwd, sender).
	resolved = registry.resolve(cwd=_CWD, sender=_SENDER, text="yes")
	assert resolved is not None
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"


@pytest.mark.asyncio
async def test_ask_human_invalid_cwd_returns_error(cfg, logger):
	"""Non-absolute cwd returns an error string without registering a pending request."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	result = await handlers.ask_human("question?", "not-absolute", _SENDER)

	assert result.startswith("ERROR: invalid cwd:")
	assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_ask_human_supersede_marks_prior_cancelled(cfg, logger):
	"""When a new ask_human for the same (cwd, sender) supersedes a prior one,
	mark_question_cancelled is called on the backend for the prior request_id."""

	class RecordingCancelBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.cancelled_ids: list[tuple[str, str]] = []

		async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
			self.cancelled_ids.append((cwd, request_id))

	backend = RecordingCancelBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# First ask — registers and blocks
	first_task = asyncio.create_task(handlers.ask_human("first question", _CWD, _SENDER))
	await asyncio.sleep(0)
	assert registry.pending_count == 1
	first_req_id = list(registry._pending.values())[0].request_id

	# Second ask for same (cwd, sender) — supersedes first
	second_task = asyncio.create_task(handlers.ask_human("second question", _CWD, _SENDER))
	await asyncio.sleep(0)

	# mark_question_cancelled must have been called for the first request_id
	assert any(rid == first_req_id for _, rid in backend.cancelled_ids)

	# Resolve the second
	registry.resolve(cwd=_CWD, sender=_SENDER, text="answer")
	await asyncio.wait_for(second_task, timeout=1.0)

	# First task was cancelled
	try:
		await asyncio.wait_for(first_task, timeout=0.1)
	except (asyncio.CancelledError, asyncio.TimeoutError):
		pass


@pytest.mark.asyncio
async def test_ask_human_cancel_marks_firebase(cfg, logger):
	"""Cancelling ask_human mid-wait calls mark_question_cancelled."""

	class RecordingCancelBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.cancelled_ids: list[tuple[str, str]] = []

		async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
			self.cancelled_ids.append((cwd, request_id))

	backend = RecordingCancelBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("cancel me", _CWD, _SENDER))
	await asyncio.sleep(0)
	req_id = list(registry._pending.values())[0].request_id

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass

	assert any(rid == req_id for _, rid in backend.cancelled_ids)
	assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_ask_human_title_passthrough(cfg, logger):
	"""title kwarg is forwarded to backend.write_channel_message for both
	the question and the at-desk notify paths."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.set_cwd_override(_CWD, True)
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(handlers.ask_human("q?", _CWD, _SENDER, title="My Task"))
	await asyncio.sleep(0)

	assert backend.channel_messages[0]["title"] == "My Task"

	registry.resolve(cwd=_CWD, sender=_SENDER, text="yes")
	await asyncio.wait_for(task, timeout=1.0)


class StaleNoticeBackend(RecordingBackend):
	"""Records send_stale_reply_notice calls."""

	def __init__(self):
		super().__init__()
		self.stale_notices: list[tuple[str, str]] = []

	async def send_stale_reply_notice(self, cwd: str, sender: str) -> None:
		self.stale_notices.append((cwd, sender))


@pytest.mark.asyncio
async def test_dispatch_loop_calls_stale_reply_notice_on_unknown_correlation(cfg, logger):
	"""When registry.resolve returns None, send_stale_reply_notice is called."""
	registry = make_registry_with_loopback()
	# No pending request registered — any response is stale.
	backend = YieldingBackend([
		IncomingResponse(correlation=(_CWD, _SENDER), text="stray reply")
	])
	stale_backend = StaleNoticeBackend()

	class CombinedBackend(StaleNoticeBackend):
		async def poll_responses(self):
			for r in [IncomingResponse(correlation=(_CWD, _SENDER), text="stray reply")]:
				yield r
			await asyncio.Event().wait()

	combined = CombinedBackend()
	sup = _make_loop_supervisor(combined, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, combined, logger, sup)
	)
	await asyncio.sleep(0.05)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	assert combined.stale_notices == [(_CWD, _SENDER)]


@pytest.mark.asyncio
async def test_dispatch_loop_deletes_stale_response_slot(cfg, logger):
	"""When registry.resolve returns None, the orphan response in `responses/`
	must be deleted so the listener doesn't re-fire it on every restart."""
	registry = make_registry_with_loopback()

	class DeleteRecordingBackend(StaleNoticeBackend):
		def __init__(self):
			super().__init__()
			self.deleted_slots: list[str] = []

		async def delete_response_slot(self, slot: str) -> None:
			self.deleted_slots.append(slot)

		async def poll_responses(self):
			yield IncomingResponse(correlation=(_CWD, _SENDER), text="stray reply", slot="r1abcd")
			await asyncio.Event().wait()

	backend = DeleteRecordingBackend()
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
	)
	await asyncio.sleep(0.05)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	assert backend.stale_notices == [(_CWD, _SENDER)]
	assert backend.deleted_slots == ["r1abcd"]


@pytest.mark.asyncio
async def test_dispatch_loop_deletes_response_slot_on_success(cfg, logger):
	"""When the response routes successfully (registry resolves), the dispatch
	loop must delete the response slot too — the cleanup must not hinge on the
	agent's ask_human coroutine surviving long enough to call
	send_resolution_confirmation, since MCP streamable-HTTP transport doesn't
	always propagate client disconnects."""
	registry = make_registry_with_loopback()
	# Pre-register a pending so registry.resolve succeeds.
	registry.add(cwd=_CWD, sender=_SENDER, request_id="r1", msg_id="m1")

	class DeleteRecordingBackend(StaleNoticeBackend):
		def __init__(self):
			super().__init__()
			self.deleted_slots: list[str] = []
			self.channel_messages: list[tuple] = []

		async def delete_response_slot(self, slot: str) -> None:
			self.deleted_slots.append(slot)

		async def write_channel_message(self, cwd, sender, message_type, content, **kwargs):
			self.channel_messages.append((cwd, sender, message_type, content, kwargs))
			return (cwd, sender), "msg-id"

		async def poll_responses(self):
			yield IncomingResponse(correlation=(_CWD, _SENDER), text="yes", slot="r1")
			await asyncio.Event().wait()

	backend = DeleteRecordingBackend()
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
	)
	# Give the dispatcher time to consume + spawn its background tasks.
	await asyncio.sleep(0.1)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	# Slot delete must be among the things the dispatcher fired off.
	assert backend.deleted_slots == ["r1"]

	# The tail "human" message must carry attached_to_msg_id pointing at the question.
	assert len(backend.channel_messages) == 1, f"expected 1 history write, got {backend.channel_messages}"
	cwd, sender, msg_type, content, kwargs = backend.channel_messages[0]
	assert sender == "John"
	assert msg_type == "human"
	assert content == "yes"
	assert kwargs.get("attached_to_msg_id") == "m1", (
		f"expected attached_to_msg_id='m1' (the question's msg_id from registry.add), "
		f"got {kwargs.get('attached_to_msg_id')!r}"
	)


@pytest.mark.asyncio
async def test_dispatch_loop_skips_slot_delete_when_slot_unknown(cfg, logger):
	"""Stale responses with no `slot` (legacy / fabricated) must not crash the
	dispatch loop — the delete is gated on slot being present."""
	registry = make_registry_with_loopback()

	class DeleteRecordingBackend(StaleNoticeBackend):
		def __init__(self):
			super().__init__()
			self.deleted_slots: list[str] = []

		async def delete_response_slot(self, slot: str) -> None:
			self.deleted_slots.append(slot)

		async def poll_responses(self):
			yield IncomingResponse(correlation=(_CWD, _SENDER), text="stray reply", slot=None)
			await asyncio.Event().wait()

	backend = DeleteRecordingBackend()
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")
	dispatch_task = asyncio.create_task(
		dispatch_responses(registry, backend, logger, sup)
	)
	await asyncio.sleep(0.05)
	dispatch_task.cancel()
	try:
		await dispatch_task
	except asyncio.CancelledError:
		pass

	assert backend.stale_notices == [(_CWD, _SENDER)]
	assert backend.deleted_slots == []
