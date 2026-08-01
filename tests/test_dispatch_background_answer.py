"""dispatch_responses' record.future is None branch must fork on
record.background: a background record routes through deliver_background_answer
(the delivery ladder), while a parked BLOCKING record keeps taking the existing
finish_parked_resolve path with unchanged behavior. Reproduced against the real
dispatch_responses loop, mirroring tests/test_dispatch_replayed_answer_false_notice.py's
fixture style (feeding backend.poll_responses).

The two helpers converge on nearly identical observable side effects when
there is no live ask or wait_queue entry for the session (same queue_notice
text, same remove_pending_question_record call), so the queue_notice/history
assertions alone would not catch a dispatcher that always calls
finish_parked_resolve. The audit log line each helper emits differs
("background_answer_delivered" vs "parked_pending_resolved") and is the actual
discriminator: it pins which helper really ran."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from server.gateway import dispatch_responses
from server.logging_jsonl import JsonlLogger
from server.messenger import IncomingResponse
from server.registry import Conversation, ConversationMember
from tests.conftest import make_registry_with_loopback, _make_loop_supervisor

_CONV = "conv-bg"
_SID = "sess-1"
_SENDER = "Agent"


async def _until(cond, cap: int = 200, dt: float = 0.005) -> bool:
	"""Pump the loop with real sleeps (so asyncio.to_thread logger writes and
	_spawn_bg tasks can complete) until cond() is true or the cap is hit."""
	for _ in range(cap):
		if cond():
			return True
		await asyncio.sleep(dt)
	return cond()


def _events(log_path):
	return [json.loads(line) for line in log_path.read_text().splitlines() if line]


def _conv(registry, conv_id=_CONV, members=()):
	conv = Conversation(id=conv_id, title="t", state="active")
	for sid, sender in members:
		conv.members_active[sid] = ConversationMember(
			cli_session_id=sid, sender=sender, cwd="c:/w", surface="windows", joined_at=0.0,
		)
	registry.conversations[conv_id] = conv
	return conv


class _SingleAnswerBackend:
	"""Yields one IncomingResponse for the given request_id, then blocks forever."""

	def __init__(self, request_id: str, text: str):
		self._request_id = request_id
		self._text = text
		self.deleted_slots: list[str] = []
		self.written_messages: list[tuple] = []
		self.removed_pending: list[tuple] = []
		self.cancelled: list[tuple] = []

	async def poll_responses(self):
		yield IncomingResponse(
			correlation=_CONV, text=self._text,
			slot=f"answers/{_CONV}/{self._request_id}", request_id=self._request_id,
		)
		await asyncio.Event().wait()

	async def delete_response_slot(self, slot):
		self.deleted_slots.append(slot)

	async def write_conversation_message(self, conversation_id, sender, kind, text, **kwargs):
		self.written_messages.append((conversation_id, sender, kind, text, kwargs))
		return ""

	async def remove_pending_question_record(self, conversation_id, request_id):
		self.removed_pending.append((conversation_id, request_id))

	async def mark_question_cancelled(self, conversation_id, request_id):
		self.cancelled.append((conversation_id, request_id))


@pytest.mark.asyncio
async def test_background_record_routes_through_delivery_ladder(tmp_path):
	"""A background record with no live ask or wait_queue entry for its session
	falls to the ladder's third rung: a queued session notice reading
	"John answered your earlier question '<question>': <answer>", the Firebase
	pending record removed, and the attached_to_msg_id history write still made.
	The audit log line ("background_answer_delivered") is what actually proves
	deliver_background_answer ran rather than finish_parked_resolve, since the
	other effects are identical between the two helpers at this rung."""
	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = make_registry_with_loopback()
	_conv(registry, members=[(_SID, _SENDER)])
	registry.add_background(_CONV, _SID, _SENDER, "req-bg", msg_id="msg-bg-1", question="Q")
	record = registry.find_by_request_id(_CONV, "req-bg")
	assert record.background is True
	assert record.future is None

	backend = _SingleAnswerBackend("req-bg", "the answer")
	session_registry = MagicMock()
	session_registry.queue_notice.return_value = True
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup, session_registry=session_registry))
	await _until(lambda: backend.removed_pending != [])
	await _until(lambda: session_registry.queue_notice.call_count >= 1)
	await _until(lambda: backend.written_messages != [])
	await _until(lambda: any("background_answer_delivered" in e.get("detail", "") for e in _events(log_path)))

	assert registry.find_by_request_id(_CONV, "req-bg") is None, "the pending record must be removed"
	assert backend.removed_pending == [(_CONV, "req-bg")]
	session_registry.queue_notice.assert_called_once_with(
		_SID, "John answered your earlier question 'Q': the answer",
	)
	assert len(backend.written_messages) == 1
	conv_id, sender, kind, text, kwargs = backend.written_messages[0]
	assert (conv_id, sender, kind, text) == (_CONV, "John", "human", "the answer")
	assert kwargs.get("attached_to_msg_id") == "msg-bg-1"
	assert backend.cancelled == [], "no live ask or wait to cancel at this rung"

	events = _events(log_path)
	assert any("background_answer_delivered" in e.get("detail", "") for e in events), (
		"deliver_background_answer must have run"
	)
	assert not any("parked_pending_resolved" in e.get("detail", "") for e in events), (
		"finish_parked_resolve must not have run for a background record"
	)

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass


@pytest.mark.asyncio
async def test_parked_blocking_record_keeps_finish_parked_resolve_behavior(tmp_path):
	"""A parked BLOCKING record (background=False) must keep taking the existing
	finish_parked_resolve path: same Firebase pending-record cleanup, same
	queued session notice text, and (the actual discriminator) the
	"parked_pending_resolved" audit log line rather than
	"background_answer_delivered". This pins the no-regression bar for the
	untouched arm of the new branch."""
	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(str(log_path))
	registry = make_registry_with_loopback()
	_conv(registry, members=[(_SID, _SENDER)])
	registry.add_parked(_CONV, _SID, _SENDER, "req-parked", msg_id="msg-parked-1", question="Q")
	record = registry.find_by_request_id(_CONV, "req-parked")
	assert record.background is False
	assert record.future is None

	backend = _SingleAnswerBackend("req-parked", "the answer")
	session_registry = MagicMock()
	session_registry.queue_notice.return_value = True
	sup = _make_loop_supervisor(backend, logger, name="dispatch_responses")

	task = asyncio.create_task(dispatch_responses(registry, backend, logger, sup, session_registry=session_registry))
	await _until(lambda: backend.removed_pending != [])
	await _until(lambda: session_registry.queue_notice.call_count >= 1)
	await _until(lambda: backend.written_messages != [])
	await _until(lambda: any("parked_pending_resolved" in e.get("detail", "") for e in _events(log_path)))

	assert registry.find_by_request_id(_CONV, "req-parked") is None
	assert backend.removed_pending == [(_CONV, "req-parked")]
	session_registry.queue_notice.assert_called_once_with(
		_SID, "John answered your earlier question 'Q': the answer",
	)
	assert len(backend.written_messages) == 1
	conv_id, sender, kind, text, kwargs = backend.written_messages[0]
	assert (conv_id, sender, kind, text) == (_CONV, "John", "human", "the answer")
	assert kwargs.get("attached_to_msg_id") == "msg-parked-1"

	events = _events(log_path)
	assert any("parked_pending_resolved" in e.get("detail", "") for e in events), (
		"finish_parked_resolve must have run"
	)
	assert not any("background_answer_delivered" in e.get("detail", "") for e in events), (
		"deliver_background_answer must not have run for a parked blocking record"
	)

	task.cancel()
	try:
		await task
	except asyncio.CancelledError:
		pass
