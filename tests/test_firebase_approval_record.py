"""add_pending_question_record's approval flag (server/firebase.py)."""

import asyncio
from unittest.mock import MagicMock

import pytest


def _make_backend(monkeypatch):
	from server import firebase as fb_module
	db = MagicMock()
	monkeypatch.setattr(fb_module, "db", db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._supervised = {}
	be._logger = None
	be._loop = asyncio.get_running_loop()
	be._storage_bucket = None
	return be, db


@pytest.mark.asyncio
async def test_an_approval_record_carries_the_flag(monkeypatch):
	be, db = _make_backend(monkeypatch)
	await be.add_pending_question_record(
		"conv-1", "apr-1", sender="Claude", msg_id="m1", question_text="Approve Bash: ls",
		suggestions=["Allow", "Deny"], cli_session_id="s1", asked_at="2026-10-12T10:00:00+00:00", approval=True,
	)
	db.reference.assert_called_with("conversations/conv-1/pending_questions/apr-1")
	record = db.reference.return_value.set.call_args.args[0]
	assert record["approval"] is True
	assert record["suggestions"] == ["Allow", "Deny"]


@pytest.mark.asyncio
async def test_an_ask_record_has_no_approval_key(monkeypatch):
	be, db = _make_backend(monkeypatch)
	await be.add_pending_question_record("conv-1", "r1", sender="Claude", msg_id="m1", question_text="Q?")
	assert "approval" not in db.reference.return_value.set.call_args.args[0]
