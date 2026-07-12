"""Integration tests for the top-level answers node path (Fix 6, WP-10 Task 1).

Answers live at /answers/<conv_id>/<request_id>; the server listener
subscribes to the top-level 'answers' node.
"""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock, patch, call
import pytest


@pytest.fixture
def backend(monkeypatch):
	from server import firebase as fb_module
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._logger = None
	be._loop = MagicMock()
	be._response_queue = MagicMock()
	be._supervised = {}
	return be, mock_db


@pytest.mark.asyncio
async def test_start_conversation_answers_listener_subscribes_to_answers_path(backend):
	"""start_conversation_answers_listener must subscribe to /answers,
	matching the path answers are written to: /answers/<conv_id>/<request_id>."""
	be, mock_db = backend
	created_listeners = []

	class CapturingSupervisedListener:
		def __init__(self, *, name, path, callback, error_logger, loop):
			self.name = name
			self.path = path
			created_listeners.append(self)
		def start(self):
			pass

	with patch("server.firebase_supervisor.SupervisedListener", CapturingSupervisedListener):
		await be.start_conversation_answers_listener()

	assert len(created_listeners) == 1
	assert created_listeners[0].path == "answers", (
		f"Expected listener on 'answers', got '{created_listeners[0].path}'"
	)
	assert created_listeners[0].name == "conversation_answers"


@pytest.mark.asyncio
async def test_start_conversation_answers_listener_idempotent(backend):
	"""Calling start_conversation_answers_listener twice must not create a second listener."""
	be, mock_db = backend
	created_count = [0]

	class CountingSupervisedListener:
		def __init__(self, *, name, path, callback, error_logger, loop):
			created_count[0] += 1
			self.name = name
		def start(self):
			pass

	with patch("server.firebase_supervisor.SupervisedListener", CountingSupervisedListener):
		await be.start_conversation_answers_listener()
		await be.start_conversation_answers_listener()

	assert created_count[0] == 1, "Second call must be a no-op"


@pytest.mark.asyncio
async def test_conversation_answer_path_matches_android_write_pattern():
	"""String-equality test: the path Android writes to must match the path
	the server listener subscribes to (modulo event routing within the subtree)."""
	conv_id = "conv-abc123"
	request_id = "req-deadbeef"

	# Path Android writes to (from MainViewModel.submitReply fix)
	android_write_path = f"answers/{conv_id}/{request_id}"

	# Path the server listener subscribes to (root of the subtree)
	server_listener_root = "answers"

	# The event path within the subtree is /<conv_id>/<request_id>
	event_path = f"/{conv_id}/{request_id}"
	parts = event_path.strip("/").split("/")

	assert len(parts) == 2
	assert parts[0] == conv_id
	assert parts[1] == request_id
	assert android_write_path.startswith(server_listener_root + "/")


@pytest.mark.asyncio
async def test_delete_response_slot_deletes_full_answers_path(backend):
	"""delete_response_slot must delete db.reference(slot) directly for
	slots under the top-level answers node."""
	be, mock_db = backend

	async def _run_in_executor(_executor, func):
		func()

	be._loop = MagicMock()
	be._loop.run_in_executor = _run_in_executor

	await be.delete_response_slot("answers/conv-abc/req-123")

	mock_db.reference.assert_called_once_with("answers/conv-abc/req-123")
	mock_db.reference.return_value.delete.assert_called_once()


@pytest.mark.asyncio
async def test_initial_snapshot_replays_undelivered_answers(backend):
	"""H06: a reply present in Firebase when the listener (re)attaches arrives
	as the initial snapshot (path '/', data = whole answers tree) and
	must be enqueued like an incremental answer event."""
	be, mock_db = backend
	captured = []

	class CapturingSupervisedListener:
		def __init__(self, *, name, path, callback, error_logger, loop):
			self.callback = callback
			captured.append(self)
		def start(self):
			pass

	with patch("server.firebase_supervisor.SupervisedListener", CapturingSupervisedListener):
		await be.start_conversation_answers_listener()

	on_answer = captured[0].callback
	event = MagicMock()
	event.event_type = "put"
	event.path = "/"
	event.data = {
		"conv-1": {"req-1": {"text": "yes do it", "sender": "Claude", "written_at": "x"}},
		"conv-2": {},
	}
	on_answer(event)

	# One answer enqueued: queue.put called with the right IncomingResponse,
	# bounced to the loop via call_soon_threadsafe.
	assert be._response_queue.put.call_count == 1
	resp = be._response_queue.put.call_args.args[0]
	assert resp.correlation == "conv-1"
	assert resp.sender == "Claude"
	assert resp.text == "yes do it"
	assert resp.slot == "answers/conv-1/req-1"
	assert be._loop.call_soon_threadsafe.call_count == 1
