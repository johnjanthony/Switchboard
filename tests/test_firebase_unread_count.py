"""Tests for write_conversation_message path assertions.

The legacy write_channel_message wrote to /channels/<cwd_key>/... with
unread_count increments. write_conversation_message writes to
/conversations/<conv_id>/messages and updates the conversation meta node.
These tests verify no /channels paths are touched for non-human messages,
and that the conversations path is written correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def backend(monkeypatch):
	from server import firebase as fb_module
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._logger = None
	be._storage_bucket = None
	return be, mock_db


@pytest.mark.asyncio
async def test_question_writes_to_conversations_not_channels(backend):
	"""write_conversation_message writes questions to /conversations/<id>/messages."""
	be, mock_db = backend

	fake_ref = MagicMock()
	fake_ref.key = "pushed-key-1"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-abc",
		"Claude",
		"question",
		"Proceed?",
		request_id="r1",
	)

	path_calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("conversations/conv-abc/messages" in c for c in path_calls)
	assert not any("channels/c:__work__sw/unread_count" in c for c in path_calls)
	assert not any("channels/c:__work__sw/pending_responses" in c for c in path_calls)


@pytest.mark.asyncio
async def test_notify_writes_to_conversations_not_channels(backend):
	"""write_conversation_message writes notify messages to /conversations/<id>/messages."""
	be, mock_db = backend

	fake_ref = MagicMock()
	fake_ref.key = "pushed-key-2"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-abc",
		"Claude",
		"notify",
		"status update",
	)

	path_calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("conversations/conv-abc/messages" in c for c in path_calls)
	assert not any("channels/c:__work__sw/unread_count" in c for c in path_calls)
	assert not any("channels/c:__work__sw/pending_responses" in c for c in path_calls)


@pytest.mark.asyncio
async def test_human_message_writes_to_conversations(backend):
	"""write_conversation_message writes human replies to /conversations/<id>/messages."""
	be, mock_db = backend

	fake_ref = MagicMock()
	fake_ref.key = "pushed-key-3"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-abc",
		"John",
		"human",
		"yes, do it",
	)

	path_calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("conversations/conv-abc/messages" in c for c in path_calls)
	assert not any("channels/" in c for c in path_calls if "unread_count" in c or "pending_responses" in c)


@pytest.mark.asyncio
async def test_non_human_message_increments_conversation_unread_count(backend):
	"""write_conversation_message increments /conversations/<id>/unread_count for non-human messages."""
	be, mock_db = backend

	fake_ref = MagicMock()
	fake_ref.key = "pushed-key-4"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-xyz",
		"Claude",
		"notify",
		"here is a status update",
	)

	path_calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("conversations/conv-xyz/unread_count" in c for c in path_calls)
	# Must NOT touch /channels/<anything>/unread_count
	assert not any("channels/" in c and "unread_count" in c for c in path_calls)


@pytest.mark.asyncio
async def test_human_message_does_not_increment_conversation_unread_count(backend):
	"""write_conversation_message does NOT increment unread_count for human (John-typed) messages."""
	be, mock_db = backend

	fake_ref = MagicMock()
	fake_ref.key = "pushed-key-5"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-xyz",
		"John",
		"human",
		"thanks, proceed",
	)

	path_calls = [str(c) for c in mock_db.reference.call_args_list]
	assert not any("unread_count" in c for c in path_calls)


@pytest.mark.asyncio
async def test_question_increments_conversation_unread_count(backend):
	"""write_conversation_message increments /conversations/<id>/unread_count for question messages."""
	be, mock_db = backend

	fake_ref = MagicMock()
	fake_ref.key = "pushed-key-6"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-q",
		"Claude",
		"question",
		"Should I continue?",
		request_id="r-1",
	)

	path_calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("conversations/conv-q/unread_count" in c for c in path_calls)
