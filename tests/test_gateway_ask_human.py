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

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		self.cancelled_questions.append((conversation_id, request_id))


class AwaitingCancelTrackingBackend(RecordingBackend):
	"""Like CancelTrackingBackend but `mark_question_cancelled` has REAL awaits
	inside, mimicking FirebaseBackend's two `asyncio.to_thread` checkpoints.
	If our handler's CancelledError block doesn't shield the cleanup, those
	checkpoints re-raise CancelledError before the recording write completes."""

	def __init__(self) -> None:
		super().__init__()
		self.cancelled_questions: list[tuple[str, str]] = []

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		# Simulate the two-checkpoint Firebase write: a read followed by a write.
		await asyncio.sleep(0)
		await asyncio.sleep(0)
		self.cancelled_questions.append((conversation_id, request_id))


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
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-cancel-awaits-001"
	scope_holder: list[anyio.CancelScope] = []

	async def run_inside_inner_scope():
		async with anyio.create_task_group() as tg:
			ask_started = anyio.Event()

			async def runner():
				with anyio.CancelScope() as scope:
					scope_holder.append(scope)
					ask_started.set()
					await handlers.ask_human("Overwrite foo?", _SENDER, cli_session_id=_SID, cwd=_CWD)

			async def canceller():
				await ask_started.wait()
				for _ in range(50):
					await anyio.sleep(0)
					if registry.pending_count > 0:
						break
				assert registry.pending_count > 0
				assert scope_holder, "runner did not capture its scope"
				scope_holder[0].cancel()

			tg.start_soon(runner)
			tg.start_soon(canceller)

	await run_inside_inner_scope()

	assert registry.pending_count == 0
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
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-cancel-inner-001"
	scope_holder: list[anyio.CancelScope] = []

	async def run_inside_inner_scope():
		async with anyio.create_task_group() as tg:
			ask_started = anyio.Event()

			async def runner():
				with anyio.CancelScope() as scope:
					scope_holder.append(scope)
					ask_started.set()
					await handlers.ask_human("Overwrite foo?", _SENDER, cli_session_id=_SID, cwd=_CWD)

			async def canceller():
				await ask_started.wait()
				# Yield until the registry has the pending entry.
				for _ in range(50):
					await anyio.sleep(0)
					if registry.pending_count > 0:
						break
				assert registry.pending_count > 0
				assert scope_holder, "runner did not capture its scope"
				scope_holder[0].cancel()

			tg.start_soon(runner)
			tg.start_soon(canceller)

	await run_inside_inner_scope()

	assert registry.pending_count == 0
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
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-cancel-anyio-001"

	async def run_inside_scope():
		async with anyio.create_task_group() as tg:
			ask_started = anyio.Event()

			async def runner():
				ask_started.set()
				await handlers.ask_human("Overwrite foo?", _SENDER, cli_session_id=_SID, cwd=_CWD)

			tg.start_soon(runner)
			await ask_started.wait()
			# Yield until the registry has the pending entry — handlers do a
			# few awaits before adding it.
			for _ in range(50):
				await anyio.sleep(0)
				if registry.pending_count > 0:
					break
			assert registry.pending_count > 0, (
				"ask_human did not register a pending entry — test setup is wrong"
			)
			tg.cancel_scope.cancel()

	await run_inside_scope()

	# Pending entry must be cleared.
	assert registry.pending_count == 0
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
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-cancel-task-001"
	task = asyncio.create_task(
		handlers.ask_human("Overwrite foo?", _SENDER, cli_session_id=_SID, cwd=_CWD)
	)
	# Yield so the handler reaches the wait_for and registers a pending entry.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert registry.pending_count > 0, (
		"ask_human did not register a pending entry — test setup is wrong"
	)

	# Direct asyncio cancel — same signal MCP would deliver if it propagated
	# the per-tool-call cancel notification from the client.
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	# Pending entry must be cleared.
	assert registry.pending_count == 0
	# mark_question_cancelled must have been called for the in-flight request_id.
	assert len(backend.cancelled_questions) == 1
	conv_id, request_id = backend.cancelled_questions[0]
	# conv_id is the auto-created conversation_id (not raw _CWD)
	assert conv_id == registry.session_to_conversation_id.get(_SID) or conv_id.startswith("conv-")
	# Request ID is generated server-side; we just assert it's a non-empty string.
	assert isinstance(request_id, str) and request_id



class YieldingBackend(RecordingBackend):
	def __init__(self, responses):
		super().__init__()
		self._responses = list(responses)

	async def poll_responses(self):
		for r in self._responses:
			yield r
		await asyncio.Event().wait()



@pytest.mark.asyncio
async def test_dispatch_loop_logs_unknown_correlation(cfg, logger, tmp_path):
	registry = make_registry_with_loopback()
	backend = YieldingBackend(
		[IncomingResponse(correlation="conv-unknown", text="stray", request_id="req-unknown", sender="Ghost")]
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
		IncomingResponse(correlation=_CWD, text="first", request_id="r1"),
		IncomingResponse(correlation=_CWD, text="second", request_id="r2"),
	])
	registry.add(conversation_id=_CWD, cli_session_id="s-1", sender=_SENDER, request_id="r2")

	call_count = {"n": 0}
	original_resolve = registry.resolve

	def flaky_resolve(conversation_id, request_id, text):
		call_count["n"] += 1
		if call_count["n"] == 1:
			raise RuntimeError("kaboom")
		return original_resolve(conversation_id, request_id, text)

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
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(
		handlers.ask_human("q", _SENDER, cli_session_id="s-cleanup-001", cwd=_CWD)
	)
	await asyncio.sleep(0)  # let it register
	await asyncio.sleep(0)
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
async def test_ask_human_supersede_marks_prior_cancelled(cfg, logger):
	"""When a new ask_human for the same (conversation, sender) supersedes a prior one,
	mark_question_cancelled is called on the backend for the prior request_id."""

	class RecordingCancelBackend(RecordingBackend):
		def __init__(self):
			super().__init__()
			self.cancelled_ids: list[tuple[str, str]] = []

		async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
			self.cancelled_ids.append((conversation_id, request_id))

	backend = RecordingCancelBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	# Both asks use the same session → same conversation_id → same registry key
	_SID = "s-supersede-001"

	# First ask — registers and blocks
	first_task = asyncio.create_task(
		handlers.ask_human("first question", _SENDER, cli_session_id=_SID, cwd=_CWD)
	)
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert registry.pending_count == 1
	first_req_id = list(registry._pending.values())[0].request_id

	# Second ask for same (conversation_id, sender) — supersedes first
	second_task = asyncio.create_task(
		handlers.ask_human("second question", _SENDER, cli_session_id=_SID, cwd=_CWD)
	)
	await asyncio.sleep(0)
	await asyncio.sleep(0)

	# mark_question_cancelled must have been called for the first request_id
	assert any(rid == first_req_id for _, rid in backend.cancelled_ids)

	# Resolve the second
	conv_id = registry.session_to_conversation_id.get(_SID)
	second_req_id = registry.pending_for_conversation(conv_id)[0].request_id
	registry.resolve(conversation_id=conv_id, request_id=second_req_id, text="answer")
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

		async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
			self.cancelled_ids.append((conversation_id, request_id))

	backend = RecordingCancelBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(
		handlers.ask_human("cancel me", _SENDER, cli_session_id="s-cancel-fb-001", cwd=_CWD)
	)
	await asyncio.sleep(0)
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
	"""title kwarg is forwarded to backend.write_conversation_message for the question."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-title-001"
	task = asyncio.create_task(
		handlers.ask_human("q?", _SENDER, title="My Task", cli_session_id=_SID, cwd=_CWD)
	)
	await asyncio.sleep(0)
	await asyncio.sleep(0)

	q_msg = next(m for m in backend.channel_messages if m["message_type"] == "question")
	assert q_msg["title"] == "My Task"

	conv_id = registry.session_to_conversation_id.get(_SID)
	req_id = registry.pending_for_conversation(conv_id)[0].request_id
	registry.resolve(conversation_id=conv_id, request_id=req_id, text="yes")
	await asyncio.wait_for(task, timeout=1.0)


class StaleNoticeBackend(RecordingBackend):
	"""Records send_stale_reply_notice calls."""

	def __init__(self):
		super().__init__()
		self.stale_notices: list[tuple[str, str]] = []

	async def send_stale_reply_notice(self, conversation_id: str, sender: str) -> None:
		self.stale_notices.append((conversation_id, sender))


@pytest.mark.asyncio
async def test_dispatch_loop_calls_stale_reply_notice_on_unknown_correlation(cfg, logger):
	"""When registry.resolve returns None, send_stale_reply_notice is called."""
	registry = make_registry_with_loopback()
	# No pending request registered — any response is stale.
	backend = YieldingBackend([
		IncomingResponse(correlation=_CWD, text="stray reply", request_id="r-stray")
	])
	stale_backend = StaleNoticeBackend()

	class CombinedBackend(StaleNoticeBackend):
		async def poll_responses(self):
			for r in [IncomingResponse(correlation=_CWD, text="stray reply", request_id="r-stray")]:
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

	assert combined.stale_notices == [(_CWD, "unknown")]


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
			yield IncomingResponse(correlation=_CWD, text="stray reply", slot="r1abcd", request_id="r-stray")
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

	assert backend.stale_notices == [(_CWD, "unknown")]
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
	registry.add(conversation_id=_CWD, cli_session_id="s-1", sender=_SENDER, request_id="r1", msg_id="m1")

	class DeleteRecordingBackend(StaleNoticeBackend):
		def __init__(self):
			super().__init__()
			self.deleted_slots: list[str] = []
			self.conv_messages: list[tuple] = []

		async def delete_response_slot(self, slot: str) -> None:
			self.deleted_slots.append(slot)

		async def write_conversation_message(self, conv_id, sender_or_message, message_type=None, text=None, **kwargs):
			if isinstance(sender_or_message, dict):
				return "msg-id"
			self.conv_messages.append((conv_id, sender_or_message, message_type, text, kwargs))
			return (conv_id, sender_or_message), "msg-id"

		async def poll_responses(self):
			yield IncomingResponse(correlation=_CWD, text="yes", slot="r1", request_id="r1")
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
	assert len(backend.conv_messages) == 1, f"expected 1 history write, got {backend.conv_messages}"
	conv_id, sender, msg_type, content, kwargs = backend.conv_messages[0]
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
			yield IncomingResponse(correlation=_CWD, text="stray reply", slot=None, request_id="r-stray")
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

	assert backend.stale_notices == [(_CWD, "unknown")]
	assert backend.deleted_slots == []


# ---------------------------------------------------------------------------
# At-desk redirect (Fix Pack 1 / Bug #1)
# ---------------------------------------------------------------------------

_AT_DESK_SENTINEL = "ERROR: John is at his desk. Ask this question via the terminal."


@pytest.mark.asyncio
async def test_ask_human_returns_at_desk_sentinel_when_away_mode_off(cfg, logger):
	"""When global away mode is OFF, ask_human must NOT block. It writes the
	question to Firebase as a one-way notify and returns the documented sentinel."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()
	registry.global_away_mode = False  # John is at his desk
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-at-desk-001"
	result = await handlers.ask_human(
		"Proceed with deletion?",
		_SENDER,
		cli_session_id=_SID,
		cwd=_CWD,
	)

	assert result == _AT_DESK_SENTINEL
	# No pending registry entry was created — we did NOT block.
	assert registry.pending_count == 0
	# The question landed on Firebase as a notify (not a question).
	assert len(backend.sent_notifications) == 1
	sender, content = backend.sent_notifications[0]
	assert sender == _SENDER
	assert content == "Proceed with deletion?"
	# No "question" type write was issued.
	assert backend.sent_questions == []


@pytest.mark.asyncio
async def test_ask_human_blocks_when_away_mode_on(cfg, logger):
	"""When global away mode is ON, ask_human still blocks on the future
	(the existing happy-path behaviour). This guards against accidentally
	flipping the at-desk redirect from a gate into a hard short-circuit."""
	backend = RecordingBackend()
	registry = make_registry_with_loopback()  # global_away_mode=True by default
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-away-on-001"
	task = asyncio.create_task(
		handlers.ask_human("Proceed?", _SENDER, cli_session_id=_SID, cwd=_CWD)
	)
	# Let the handler reach wait_for and register the pending entry.
	await asyncio.sleep(0)
	await asyncio.sleep(0)
	assert registry.pending_count == 1

	# Resolve and confirm the await completes with the resolution text.
	conv_id = registry.session_to_conversation_id.get(_SID)
	req_id = registry.pending_for_conversation(conv_id)[0].request_id
	registry.resolve(conversation_id=conv_id, request_id=req_id, text="yes")
	result = await asyncio.wait_for(task, timeout=1.0)
	assert result == "yes"


class PendingQuestionTrackingBackend(RecordingBackend):
	"""Records pending_questions and answered_question_msg_ids writes so we can
	assert the 2026-05-19 spec subtrees are maintained across the ask_human
	lifecycle."""

	def __init__(self) -> None:
		super().__init__()
		self.pending_added: list[dict] = []
		self.pending_removed: list[tuple[str, str]] = []
		self.cancelled_questions: list[tuple[str, str]] = []
		self.answered_marked: list[tuple[str, str]] = []

	async def mark_question_cancelled(self, conversation_id: str, request_id: str) -> None:
		self.cancelled_questions.append((conversation_id, request_id))

	async def add_pending_question_record(
		self,
		conversation_id: str,
		request_id: str,
		*,
		sender: str,
		msg_id,
		question_text: str,
		suggestions=None,
		cli_session_id=None,
		asked_at=None,
	) -> None:
		self.pending_added.append({
			"conversation_id": conversation_id,
			"request_id": request_id,
			"sender": sender,
			"msg_id": msg_id,
			"question_text": question_text,
			"suggestions": suggestions,
			"cli_session_id": cli_session_id,
			"asked_at": asked_at,
		})

	async def remove_pending_question_record(
		self,
		conversation_id: str,
		request_id: str,
	) -> None:
		self.pending_removed.append((conversation_id, request_id))

	async def mark_question_answered(
		self,
		conversation_id: str,
		msg_id: str,
	) -> None:
		self.answered_marked.append((conversation_id, msg_id))


@pytest.mark.asyncio
async def test_ask_human_writes_pending_questions_and_clears_on_reply(cfg, logger):
	"""ask_human must write /conversations/<id>/pending_questions/<request_id> when
	the question goes out, and delete it when the reply lands.
	answered_question_msg_ids is NOT written: the phone derives answered-state
	from message flags, so that subtree has no reader (F-66/F-73, retired)."""
	backend = PendingQuestionTrackingBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	_SID = "s-pending-001"
	task = asyncio.create_task(
		handlers.ask_human("Proceed?", _SENDER, cli_session_id=_SID, cwd=_CWD)
	)
	# Give the handler a couple ticks to reach wait_for and queue the bg write.
	for _ in range(6):
		await asyncio.sleep(0)
	assert registry.pending_count == 1
	# pending_questions record written
	assert len(backend.pending_added) == 1
	added = backend.pending_added[0]
	assert added["sender"] == _SENDER
	assert added["question_text"] == "Proceed?"

	conv_id = registry.session_to_conversation_id.get(_SID)
	registry.resolve(conversation_id=conv_id, request_id=added["request_id"], text="yes")
	result = await asyncio.wait_for(task, timeout=1.0)
	# Let bg writes complete.
	for _ in range(6):
		await asyncio.sleep(0)
	assert result == "yes"
	# pending_questions cleared on successful resolution
	assert (conv_id, added["request_id"]) in backend.pending_removed
	# answered_question_msg_ids is NOT written (F-66/F-73, retired)
	assert backend.answered_marked == []


@pytest.mark.asyncio
async def test_ask_human_persists_session_id_and_ask_timestamp(cfg, logger):
	"""Chunk 7: the pending_questions record must carry the asking session's id
	and the ask timestamp so hydration can rebuild it as a parked pending."""
	backend = PendingQuestionTrackingBackend()
	registry = make_registry_with_loopback()
	handlers = build_tool_handlers(cfg, registry, backend, logger)

	task = asyncio.create_task(
		handlers.ask_human("Proceed?", _SENDER, cli_session_id="s-park-001", cwd=_CWD)
	)
	for _ in range(6):
		await asyncio.sleep(0)
	assert len(backend.pending_added) == 1
	added = backend.pending_added[0]
	assert added["cli_session_id"] == "s-park-001"
	assert isinstance(added["asked_at"], str) and added["asked_at"]

	conv_id = registry.session_to_conversation_id.get("s-park-001")
	registry.resolve(conversation_id=conv_id, request_id=added["request_id"], text="ok")
	await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_ask_human_removes_pending_questions_on_timeout(cfg, logger):
	"""On TimeoutError, the pending_questions record must be cleaned up."""
	backend = PendingQuestionTrackingBackend()
	registry = make_registry_with_loopback()
	# Force a tiny timeout
	tiny_cfg = Config(
		host=cfg.host, port=cfg.port,
		timeout_seconds=0,
		log_path=cfg.log_path,
	)
	handlers = build_tool_handlers(tiny_cfg, registry, backend, logger)

	_SID = "s-pending-timeout-001"
	result = await handlers.ask_human("Hello?", _SENDER, cli_session_id=_SID, cwd=_CWD)
	# Allow bg writes to finish
	for _ in range(6):
		await asyncio.sleep(0)
	# The TIMEOUT_SENTINEL is whatever the handler returns; whatever the value,
	# the cleanup write must have been queued for the request that timed out.
	assert backend.pending_added, "pending_questions record was never written"
	req_id = backend.pending_added[0]["request_id"]
	conv_id = registry.session_to_conversation_id.get(_SID)
	# Timeout now calls mark_question_cancelled (superset of remove_pending_question_record):
	# sets the cancelled flag on the phone AND removes the pending_questions record.
	assert (conv_id, req_id) in backend.cancelled_questions
	# answered should NOT have been marked — the question wasn't answered.
	assert backend.answered_marked == []
