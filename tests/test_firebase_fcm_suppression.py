"""REV-109: write_conversation_message(suppress_push=True) must skip the FCM
send while still writing the message payload (silent unread bump)."""

from __future__ import annotations

import asyncio

import pytest

from tests.test_firebase_document_upload import _make_backend


def _install_fcm_recorder(be):
	calls = []

	async def _record_fcm(*args, **kwargs):
		calls.append(args)

	be._send_fcm = _record_fcm
	return calls


@pytest.mark.asyncio
async def test_agent_msg_pushes_fcm_by_default(monkeypatch):
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)
	calls = _install_fcm_recorder(be)

	await be.write_conversation_message("conv-1", "Claude-A", "agent_msg", "hi", format="markdown")

	assert len(calls) == 1


@pytest.mark.asyncio
async def test_suppress_push_skips_fcm_but_still_writes(monkeypatch):
	from server import firebase as fb_module

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)
	calls = _install_fcm_recorder(be)

	result = await be.write_conversation_message(
		"conv-1", "Claude-A", "agent_msg", "hi", format="markdown", suppress_push=True,
	)

	assert calls == []
	# The message write itself still happened (db is a MagicMock; the push()
	# ref's set() was invoked) and the method returned normally.
	assert result is not None
	assert fb_module.db.reference.called


@pytest.mark.asyncio
async def test_away_mode_gate_matrix(monkeypatch):
	from server import firebase as fb_module

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)
	calls = _install_fcm_recorder(be)
	
	# Default provider: away mode ON
	be._away_mode_provider = lambda: True

	# 1. Away ON, non-allowlist => FCM SENT
	await be.write_conversation_message("conv-1", "Claude-A", "agent_msg", "hi")
	assert len(calls) == 1
	calls.clear()

	# 2. Away ON, allowlist => FCM SENT
	await be.write_conversation_message("conv-1", "John", "question", "hi")
	assert len(calls) == 1
	calls.clear()

	# Change provider: away mode OFF
	be._away_mode_provider = lambda: False

	# 3. Away OFF, allowlist ("question") => FCM SENT
	await be.write_conversation_message("conv-1", "John", "question", "hi")
	assert len(calls) == 1
	calls.clear()
	
	# 4. Away OFF, allowlist ("notify") => FCM SENT
	await be.write_conversation_message("conv-1", "system", "notify", "hi")
	assert len(calls) == 1
	calls.clear()

	# 5. Away OFF, non-allowlist ("agent_msg") => NO FCM
	await be.write_conversation_message("conv-1", "Claude-A", "agent_msg", "hi")
	assert len(calls) == 0

	# 6. Away OFF, non-allowlist ("parting") => NO FCM
	await be.write_conversation_message("conv-1", "Claude-A", "parting", "bye")
	assert len(calls) == 0
