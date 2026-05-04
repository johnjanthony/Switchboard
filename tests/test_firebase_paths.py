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
async def test_global_away_mirror_writes_new_path(backend):
	be, mock_db = backend
	await be.write_away_mode_mirror(None, True)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/away_mode" in c for c in calls)
	assert not any("away_mode/global" in c for c in calls)


@pytest.mark.asyncio
async def test_per_cwd_override_writes_channel_field(backend):
	be, mock_db = backend
	await be.write_away_mode_mirror("c:/work/sw", True)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("channels/c:__work__sw/away_mode" in c for c in calls)
	assert not any("away_mode/overrides" in c for c in calls)


@pytest.mark.asyncio
async def test_per_cwd_override_remove_deletes_channel_field(backend):
	be, mock_db = backend
	await be.write_away_mode_mirror("c:/work/sw", None)
	# The reference call for the channel away_mode path was made; the .delete()
	# should have been called on the returned Reference mock.
	any_channel_ref_call = any(
		"channels/c:__work__sw/away_mode" in str(c)
		for c in mock_db.reference.call_args_list
	)
	assert any_channel_ref_call
	# .delete() was called at least once on the mock reference chain
	mock_db.reference.return_value.delete.assert_called()


@pytest.mark.asyncio
async def test_wipe_channel_deletes_pending_responses_and_away_mode(backend):
	be, mock_db = backend
	await be.wipe_channel("c:/work/sw")
	deleted_paths = [str(c) for c in mock_db.reference.call_args_list]
	assert any("channels/c:__work__sw/pending_responses" in p for p in deleted_paths)
	assert any("channels/c:__work__sw/away_mode" in p for p in deleted_paths)


@pytest.mark.asyncio
async def test_reset_all_away_mode_writes_global_false_and_clears_overrides(backend):
	"""Startup reset: global goes to false; every channel with away_mode set
	gets its field deleted. Channels without away_mode are not touched."""
	be, mock_db = backend
	mock_db.reference.return_value.get.return_value = {
		"c:__work__sw": {"away_mode": True, "title": "Switchboard"},
		"c:__work__hygiene": {"away_mode": False},
		"c:__work__no_override": {"title": "no override here"},
	}
	await be.reset_all_away_mode()

	# Multi-path update payload should hit global=False and delete both
	# channels' away_mode fields, while leaving the no-override channel alone.
	update_calls = [c for c in mock_db.reference.return_value.update.call_args_list]
	assert len(update_calls) >= 1
	payload = update_calls[-1].args[0]
	assert payload.get("global_settings/away_mode") is False
	assert "channels/c:__work__sw/away_mode" in payload
	assert payload["channels/c:__work__sw/away_mode"] is None
	assert "channels/c:__work__hygiene/away_mode" in payload
	assert payload["channels/c:__work__hygiene/away_mode"] is None
	assert "channels/c:__work__no_override/away_mode" not in payload


@pytest.mark.asyncio
async def test_reset_all_away_mode_with_no_channels_still_writes_global_false(backend):
	be, mock_db = backend
	mock_db.reference.return_value.get.return_value = None
	await be.reset_all_away_mode()
	payload = mock_db.reference.return_value.update.call_args.args[0]
	assert payload == {"global_settings/away_mode": False}


@pytest.mark.asyncio
async def test_write_channel_message_persists_attached_to_msg_id(backend):
	"""When attached_to_msg_id is passed, it lands on the Firebase message payload."""
	be, mock_db = backend
	captured_payloads = []

	fake_ref = MagicMock()
	fake_ref.key = "msg-id-fake"
	fake_ref.set.side_effect = lambda payload: captured_payloads.append(payload)
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_channel_message(
		cwd="/tmp/test",
		sender="John",
		message_type="human",
		content="reply text",
		attached_to_msg_id="question-msg-id-123",
	)

	assert len(captured_payloads) == 1
	assert captured_payloads[0]["attached_to_msg_id"] == "question-msg-id-123"


@pytest.mark.asyncio
async def test_write_channel_message_omits_attached_to_msg_id_when_not_passed(backend):
	"""When attached_to_msg_id is None, the field is absent from the payload (no null writes)."""
	be, mock_db = backend
	captured_payloads = []

	fake_ref = MagicMock()
	fake_ref.key = "msg-id-fake"
	fake_ref.set.side_effect = lambda payload: captured_payloads.append(payload)
	mock_db.reference.return_value.push.return_value = fake_ref

	await be.write_channel_message(
		cwd="/tmp/test",
		sender="Claude",
		message_type="notify",
		content="status update",
	)

	assert len(captured_payloads) == 1
	assert "attached_to_msg_id" not in captured_payloads[0]
