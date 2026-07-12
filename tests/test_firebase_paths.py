"""Tests for Firebase path layout under the new schema."""

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
	return be, mock_db


@pytest.mark.asyncio
async def test_reset_all_away_mode_writes_global_false(backend):
	"""Startup reset: global goes to false. Per-channel overrides retired —
	the function no longer touches /channels/*/away_mode."""
	be, mock_db = backend
	await be.reset_all_away_mode()
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/away_mode" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with(False)
	# Must NOT walk /channels or issue multi-path updates against channel away_mode.
	assert not any("channels/" in c and "away_mode" in c for c in calls)


@pytest.mark.asyncio
async def test_write_conversation_message_persists_attached_to_msg_id(backend):
	"""When attached_to_msg_id is passed, it lands on the Firebase message payload."""
	be, mock_db = backend
	captured_payloads = []

	fake_ref = MagicMock()
	fake_ref.key = "msg-id-fake"
	fake_ref.set.side_effect = lambda payload: captured_payloads.append(payload)
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-test-123",
		"John",
		"human",
		"reply text",
		attached_to_msg_id="question-msg-id-123",
	)

	assert len(captured_payloads) == 1
	assert captured_payloads[0]["attached_to_msg_id"] == "question-msg-id-123"


@pytest.mark.asyncio
async def test_write_conversation_message_omits_attached_to_msg_id_when_not_passed(backend):
	"""When attached_to_msg_id is None, the field is absent from the payload (no null writes)."""
	be, mock_db = backend
	captured_payloads = []

	fake_ref = MagicMock()
	fake_ref.key = "msg-id-fake"
	fake_ref.set.side_effect = lambda payload: captured_payloads.append(payload)
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-test-456",
		"Claude",
		"notify",
		"status update",
	)

	assert len(captured_payloads) == 1
	assert "attached_to_msg_id" not in captured_payloads[0]


# ---------------------------------------------------------------------------
# Task 29: new-schema Firebase method tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_open_conversation_node_deletes_the_node(backend):
	"""One-shot chunk 5 cleanup: delete_open_conversation_node deletes
	global_settings/open_conversation_id via ref.delete(), sending the phone's
	open-accent listener a permanent null."""
	be, mock_db = backend
	await be.delete_open_conversation_node()
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/open_conversation_id" in c for c in calls)
	mock_db.reference.return_value.delete.assert_called_once()


@pytest.mark.asyncio
async def test_set_global_away_mode_writes_to_global_settings(backend):
	"""set_global_away_mode writes bool to global_settings/away_mode."""
	be, mock_db = backend
	await be.set_global_away_mode(True)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/away_mode" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with(True)


@pytest.mark.asyncio
async def test_set_session_home_writes_to_cli_sessions(backend):
	"""set_session_home writes to cli_sessions/<session_id>/home_conversation_id."""
	be, mock_db = backend
	await be.set_session_home("sess-xyz", "conv-home-1")
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("cli_sessions/sess-xyz/home_conversation_id" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with("conv-home-1")


@pytest.mark.asyncio
async def test_set_conversation_state_writes_state(backend):
	"""set_conversation_state writes state string to conversations/<id>/meta/state.
	Hydration reads from meta.state — writing top-level /state would leave ended
	conversations resurrecting as Active on restart."""
	be, mock_db = backend
	await be.set_conversation_state("conv-99", "ended")
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("conversations/conv-99/meta/state" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with("ended")


# ---------------------------------------------------------------------------
# Item 6: set_global_wsl_available tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_global_wsl_available_writes_to_global_settings(backend):
	"""set_global_wsl_available(True) writes True to global_settings/wsl_available."""
	be, mock_db = backend
	await be.set_global_wsl_available(True)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/wsl_available" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with(True)


@pytest.mark.asyncio
async def test_set_global_wsl_available_writes_false(backend):
	"""set_global_wsl_available(False) writes False to global_settings/wsl_available."""
	be, mock_db = backend
	await be.set_global_wsl_available(False)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/wsl_available" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with(False)


@pytest.mark.asyncio
async def test_set_global_wsl_available_coerces_truthy(backend):
	"""set_global_wsl_available coerces truthy/falsy to bool before writing."""
	be, mock_db = backend
	# A non-empty string is truthy — should write True
	await be.set_global_wsl_available("/home/john")
	mock_db.reference.return_value.set.assert_called_with(True)

	mock_db.reset_mock()
	# None is falsy — should write False
	await be.set_global_wsl_available(None)
	mock_db.reference.return_value.set.assert_called_with(False)


# ---------------------------------------------------------------------------
# Migration: write_conversation_message path-assertion tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_conversation_message_writes_to_correct_path(backend):
	"""write_conversation_message (expanded form) writes to messages/<id>."""
	be, mock_db = backend
	fake_ref = MagicMock()
	fake_ref.key = "push-key-abc"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message("conv-test-path", "Claude", "notify", "hello")

	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("messages/conv-test-path" in c for c in calls)
	# Must NOT write to /channels/<anything>/messages
	assert not any("channels/" in c and "/messages" in c for c in calls)


@pytest.mark.asyncio
async def test_write_conversation_message_question_includes_request_id(backend):
	"""When request_id is supplied, the payload includes it."""
	be, mock_db = backend
	captured = []
	fake_ref = MagicMock()
	fake_ref.key = "push-q"
	fake_ref.set.side_effect = lambda p: captured.append(p)
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_conversation_message(
		"conv-q", "Claude", "question", "Is this ok?", request_id="r-abc123"
	)

	assert len(captured) == 1
	assert captured[0]["request_id"] == "r-abc123"
	assert captured[0]["type"] == "question"
	assert captured[0]["sender"] == "Claude"


@pytest.mark.asyncio
async def test_write_conversation_message_returns_correlation_and_msg_id(backend):
	"""Expanded form returns (correlation, msg_id) matching what ask_human expects."""
	be, mock_db = backend
	fake_ref = MagicMock()
	fake_ref.key = "push-key-xyz"
	mock_db.reference.return_value.push.return_value = fake_ref

	result = await be.write_conversation_message(
		"conv-ret", "Claude", "question", "Ready?", request_id="req-99"
	)

	# Must be a 2-tuple
	assert isinstance(result, tuple) and len(result) == 2
	conv_id, msg_id = result
	# correlation is the conversation id; answers resolve by (conv_id, request_id)
	assert conv_id == "conv-ret"
	assert msg_id == "push-key-xyz"


@pytest.mark.asyncio
async def test_write_conversation_message_dict_form_returns_push_key(backend):
	"""Legacy dict form write_conversation_message(conv_id, message_dict) returns a str push key."""
	be, mock_db = backend
	fake_ref = MagicMock()
	fake_ref.key = "push-dict-key"
	mock_db.reference.return_value.push.return_value = fake_ref

	msg = {"seq": 0, "sender": "system", "type": "system", "text": "hello", "timestamp": "2026-01-01T00:00:00+00:00"}
	result = await be.write_conversation_message("conv-dict", msg)

	assert result == "push-dict-key"
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("messages/conv-dict" in c for c in calls)


@pytest.mark.asyncio
async def test_no_channels_messages_path_written_for_conversation_message(backend):
	"""write_conversation_message must never touch /channels/<cwdKey>/messages."""
	be, mock_db = backend
	fake_ref = MagicMock()
	fake_ref.key = "push-key-clean"
	mock_db.reference.return_value.push.return_value = fake_ref

	for msg_type in ("notify", "question", "human", "agent_msg", "parting"):
		mock_db.reset_mock()
		await be.write_conversation_message("conv-clean", "Claude", msg_type, "text", request_id="r1" if msg_type == "question" else None)
		calls = [str(c) for c in mock_db.reference.call_args_list]
		channel_msg_writes = [c for c in calls if "channels/" in c and "/messages" in c]
		assert channel_msg_writes == [], f"Unexpected /channels/*/messages write for type={msg_type}: {channel_msg_writes}"


@pytest.mark.asyncio
async def test_mark_question_cancelled_reads_from_conversations_not_channels(backend):
	"""mark_question_cancelled now scans /messages/<conv_id>, not /channels/."""
	be, mock_db = backend
	# Simulate Firebase returning no messages (empty conversations node)
	mock_db.reference.return_value.get.return_value = None

	await be.mark_question_cancelled("conv-cancel", "req-xyz")

	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("messages/conv-cancel" in c for c in calls)
	assert not any("channels/" in c and "/messages" in c for c in calls)


# ---------------------------------------------------------------------------
# Admin notifications path migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_admin_notification_writes_to_admin_notifications_path(backend):
	"""write_admin_notification pushes to /admin_notifications, NOT /channels/_admin/messages."""
	be, mock_db = backend
	fake_ref = MagicMock()
	fake_ref.key = "push-admin-key"
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_admin_notification("Server restarting in 30 seconds")

	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("admin_notifications" in c for c in calls), f"Expected admin_notifications in calls: {calls}"
	assert not any("channels/_admin" in c for c in calls), f"Unexpected legacy path in calls: {calls}"


@pytest.mark.asyncio
async def test_write_admin_notification_payload_shape(backend):
	"""write_admin_notification writes sender, type, text, format, and timestamp."""
	be, mock_db = backend
	captured = []
	fake_ref = MagicMock()
	fake_ref.key = "push-admin-key-2"
	fake_ref.set.side_effect = lambda p: captured.append(p)
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_admin_notification("Startup error detected")

	assert len(captured) == 1
	payload = captured[0]
	assert payload["sender"] == "system"
	assert payload["type"] == "notify"
	assert payload["text"] == "Startup error detected"
	assert payload["format"] == "markdown"
	assert "timestamp" in payload


# ---------------------------------------------------------------------------
# T-180: widget hub writers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_widget_rings_sets_widget_rings(backend):
	be, mock_db = backend
	await be.write_widget_rings({"s1": {"pct": 0.4}})
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("widget/rings" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with({"s1": {"pct": 0.4}})


@pytest.mark.asyncio
async def test_write_widget_rings_empty_sets_empty_map(backend):
	be, mock_db = backend
	await be.write_widget_rings({})
	mock_db.reference.return_value.set.assert_called_with({})


@pytest.mark.asyncio
async def test_write_widget_quota_sets_value(backend):
	be, mock_db = backend
	await be.write_widget_quota({"session": {"pct": 0.5}})
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("widget/quota" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with({"session": {"pct": 0.5}})


@pytest.mark.asyncio
async def test_write_widget_quota_none_deletes(backend):
	# firebase_admin rejects set(None); clearing must use delete().
	be, mock_db = backend
	await be.write_widget_quota(None)
	mock_db.reference.return_value.delete.assert_called_once()
	mock_db.reference.return_value.set.assert_not_called()


@pytest.mark.asyncio
async def test_write_widget_pushed_at_sets_string(backend):
	be, mock_db = backend
	await be.write_widget_pushed_at("2026-06-25T12:00:00+00:00")
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("widget/pushed_at" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with("2026-06-25T12:00:00+00:00")


@pytest.mark.asyncio
async def test_write_widget_status_sets_widget_status(backend):
	be, mock_db = backend
	payload = {"level": "major", "watch_state": "watching", "button": "stop",
			   "description": "Partial outage", "incidents": ["X"], "fetched_at": "2026-06-25T12:00:00+00:00"}
	await be.write_widget_status(payload)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("widget/status" in c for c in calls)
	mock_db.reference.return_value.set.assert_called_with(payload)
