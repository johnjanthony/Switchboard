"""Tests for unread_count atomic increment in write_message."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest


@pytest.mark.asyncio
async def test_question_increments_unread_count_only(monkeypatch):
	"""Question writes bump unread_count but NOT pending_responses.

	pending_responses is owned by the Registry pending-mirror callback, which
	fires on registry.add (+1) / resolve / remove / cancel (-1). Doing it here
	too would double-count every question."""
	from server import firebase as fb_module

	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	monkeypatch.setattr(fb_module, "_increment", lambda n: f"INCR({n})")

	backend = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	backend._logger = None

	await backend.write_channel_message(
		cwd="c:/work/sw",
		sender="Claude",
		message_type="question",
		content="hi",
		request_id="r1",
	)

	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("channels/c:__work__sw/unread_count" in c for c in calls)
	assert not any("channels/c:__work__sw/pending_responses" in c for c in calls)


@pytest.mark.asyncio
async def test_notify_increments_unread_count_only(monkeypatch):
	from server import firebase as fb_module

	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	monkeypatch.setattr(fb_module, "_increment", lambda n: f"INCR({n})")

	backend = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	backend._logger = None

	await backend.write_channel_message(
		cwd="c:/work/sw",
		sender="Claude",
		message_type="notify",
		content="hi",
	)

	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("channels/c:__work__sw/unread_count" in c for c in calls)
	assert not any("channels/c:__work__sw/pending_responses" in c for c in calls)


@pytest.mark.asyncio
async def test_human_message_increments_neither(monkeypatch):
	from server import firebase as fb_module

	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	monkeypatch.setattr(fb_module, "_increment", lambda n: f"INCR({n})")

	backend = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	backend._logger = None

	await backend.write_channel_message(
		cwd="c:/work/sw",
		sender="Human",
		message_type="human",
		content="hi",
	)

	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert not any("channels/c:__work__sw/unread_count" in c for c in calls)
	assert not any("channels/c:__work__sw/pending_responses" in c for c in calls)
