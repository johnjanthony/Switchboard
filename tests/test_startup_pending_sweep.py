"""P1-1 (H05): on startup, every conversations/*/pending_questions record is
an orphan (futures died with the old process). The sweep cancels each one via
mark_question_cancelled so the phone's pending list and bulk-respond modal
drop them."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def backend(monkeypatch):
	from server import firebase as fb_module

	def reference(path: str):
		ref = MagicMock()
		if path == "conversations":
			ref.get.return_value = {"conv-1": True, "conv-2": True, "conv-3": True}
		elif path == "conversations/conv-1/pending_questions":
			ref.get.return_value = {"req-a": {"sender": "Claude"}, "req-b": {"sender": "Gemini"}}
		elif path == "conversations/conv-2/pending_questions":
			ref.get.return_value = None  # no pendings
		elif path == "conversations/conv-3/pending_questions":
			ref.get.return_value = {"req-c": {"sender": "Claude"}}
		else:
			ref.get.return_value = None
		return ref

	mock_db = MagicMock()
	mock_db.reference = reference
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._logger = None
	be.mark_question_cancelled = AsyncMock()
	return be


@pytest.mark.asyncio
async def test_sweep_cancels_every_orphaned_record(backend):
	count = await backend.sweep_orphaned_pending_questions()
	assert count == 3
	cancelled = {c.args for c in backend.mark_question_cancelled.await_args_list}
	assert cancelled == {("conv-1", "req-a"), ("conv-1", "req-b"), ("conv-3", "req-c")}


@pytest.mark.asyncio
async def test_sweep_with_no_conversations_is_a_noop(backend, monkeypatch):
	from server import firebase as fb_module
	empty_db = MagicMock()
	empty_db.reference = lambda path: MagicMock(get=MagicMock(return_value=None))
	monkeypatch.setattr(fb_module, "db", empty_db)
	count = await backend.sweep_orphaned_pending_questions()
	assert count == 0
	backend.mark_question_cancelled.assert_not_awaited()
