"""REV-104: a member move is ONE atomic multi-location Firebase update, so a
crash can never leave the member mirrored in both conversations."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from server.firebase import FirebaseBackend
from server.registry import ConversationMember


def _member():
	return ConversationMember(
		cli_session_id="s-1", sender="Claude 2", cwd="C:/Work/X",
		surface="windows", joined_at=123.0, last_seen_seq=7,
	)


def _capture_update():
	mock_ref = MagicMock()
	mock_db = MagicMock()
	mock_db.reference = MagicMock(return_value=mock_ref)
	return mock_db, mock_ref


def test_move_composes_single_multi_location_update():
	async def run():
		mock_db, mock_ref = _capture_update()
		member = _member()
		with patch("server.firebase.db", mock_db):
			backend = FirebaseBackend.__new__(FirebaseBackend)  # no __init__: method touches only the db module
			await backend.move_conversation_member("conv-src", "conv-tgt", member, "Claude", end_source=True)
		mock_ref.update.assert_called_once()
		updates = mock_ref.update.call_args.args[0]
		assert updates["conversations/conv-src/members_active/Claude"] is None
		assert updates["conversations/conv-src/meta/state"] == "ended"
		payload = updates["conversations/conv-tgt/members_active/Claude 2"]
		assert payload["cli_session_id"] == "s-1"
		assert payload["sender"] == "Claude 2"
		assert payload["last_seen_seq"] == 7
		assert set(updates) == {
			"conversations/conv-src/members_active/Claude",
			"conversations/conv-tgt/members_active/Claude 2",
			"conversations/conv-src/meta/state",
		}
	asyncio.run(run())


def test_move_without_end_source_omits_state_path():
	async def run():
		mock_db, mock_ref = _capture_update()
		with patch("server.firebase.db", mock_db):
			backend = FirebaseBackend.__new__(FirebaseBackend)
			await backend.move_conversation_member("conv-src", "conv-tgt", _member(), "Claude")
		updates = mock_ref.update.call_args.args[0]
		assert "conversations/conv-src/meta/state" not in updates
		assert len(updates) == 2
	asyncio.run(run())


def test_ending_the_source_writes_ended_at_in_the_same_update():
	async def run():
		mock_db, mock_ref = _capture_update()
		with patch("server.firebase.db", mock_db):
			backend = FirebaseBackend.__new__(FirebaseBackend)
			await backend.move_conversation_member(
				"conv-src", "conv-tgt", _member(), "Claude", end_source=True, ended_at=123.0,
			)
		mock_ref.update.assert_called_once()
		updates = mock_ref.update.call_args.args[0]
		assert updates["conversations/conv-src/meta/state"] == "ended"
		assert updates["conversations/conv-src/meta/ended_at"] == 123.0
	asyncio.run(run())


def test_set_conversation_state_with_ended_at_is_one_update_carrying_both_keys():
	async def run():
		mock_db, mock_ref = _capture_update()
		with patch("server.firebase.db", mock_db):
			backend = FirebaseBackend.__new__(FirebaseBackend)
			await backend.set_conversation_state("conv-1", "ended", ended_at=123.0)
		mock_db.reference.assert_called_once_with("conversations/conv-1/meta")
		mock_ref.update.assert_called_once_with({"state": "ended", "ended_at": 123.0})
		mock_ref.set.assert_not_called()
	asyncio.run(run())
