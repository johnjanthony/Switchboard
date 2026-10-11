"""A question's phone notification clears when the question ends: the cancel
side through mark_question_cancelled, the answered side through John's reply
attached to the question card. Wear OS bridging clears the watch's copy."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.gateway.bg_tasks import drain_bg_tasks
from tests.test_firebase_document_upload import _make_backend


def _record_dismissals(be):
	calls = []
	be._dismiss_question_notification = lambda conv_id, msg_id: calls.append((conv_id, msg_id))
	return calls


@pytest.mark.asyncio
async def test_dismissal_is_a_normal_priority_data_message_to_the_questions_topic(monkeypatch):
	from server import firebase as fb_module
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	sent = []
	monkeypatch.setattr(fb_module.messaging, "send", lambda msg: sent.append(msg) or "projects/p/messages/1")

	await be._send_question_dismissal("conv-1", "-OQcard1")

	assert len(sent) == 1
	msg = sent[0]
	assert msg.topic == "questions"
	assert msg.data == {"conv_id": "conv-1", "sb_message_type": "dismiss", "message_id": "-OQcard1"}
	assert msg.android.priority == "normal"
	assert msg.notification is None


@pytest.mark.asyncio
async def test_a_failed_dismissal_is_logged_not_raised(monkeypatch):
	from server import firebase as fb_module
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	be._logger = MagicMock()
	be._logger.surface_error = AsyncMock()

	def _boom(msg):
		raise RuntimeError("fcm down")
	monkeypatch.setattr(fb_module.messaging, "send", _boom)

	await be._send_question_dismissal("conv-1", "-OQcard1")

	be._logger.surface_error.assert_awaited_once()
	assert "firebase_fcm_dismiss_error" in be._logger.surface_error.await_args.args[0]


@pytest.mark.asyncio
async def test_dismissing_runs_the_send_in_the_background(monkeypatch):
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	be._send_question_dismissal = AsyncMock()

	assert be._dismiss_question_notification("conv-1", "-OQcard1") is None
	await drain_bg_tasks(timeout=2)

	be._send_question_dismissal.assert_awaited_once_with("conv-1", "-OQcard1")


@pytest.mark.asyncio
async def test_cancelling_a_question_dismisses_its_card(monkeypatch):
	from server import firebase as fb_module
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	fb_module.db.reference.return_value.get.return_value = {
		"-OQother": {"request_id": "req-0", "type": "question"},
		"-OQcard1": {"request_id": "req-1", "type": "question"},
	}
	calls = _record_dismissals(be)

	await be.mark_question_cancelled("conv-1", "req-1")

	assert calls == [("conv-1", "-OQcard1")]


@pytest.mark.asyncio
async def test_cancelling_with_no_card_sends_no_dismissal(monkeypatch):
	from server import firebase as fb_module
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	fb_module.db.reference.return_value.get.return_value = {"-OQother": {"request_id": "req-0"}}
	calls = _record_dismissals(be)

	await be.mark_question_cancelled("conv-1", "req-1")

	assert calls == []


@pytest.mark.asyncio
async def test_a_reply_attached_to_a_question_dismisses_it(monkeypatch):
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	calls = _record_dismissals(be)

	await be.write_conversation_message("conv-1", "John", "human", "yes", attached_to_msg_id="-OQcard1")

	assert calls == [("conv-1", "-OQcard1")]


@pytest.mark.asyncio
async def test_a_human_message_without_an_attached_question_dismisses_nothing(monkeypatch):
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	calls = _record_dismissals(be)

	await be.write_conversation_message("conv-1", "John", "human", "stop for now", suppress_push=True)

	assert calls == []
