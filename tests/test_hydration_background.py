"""Tests for hydration routing of background pending records."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from server.registry import Registry
from tests.test_hydration import make_firebase_db_mock, make_logger, _conv_with_pending


@pytest.mark.asyncio
async def test_hydrate_routes_background_record_into_background_pending():
	"""A pending_questions record carrying background: True rebuilds into
	_background_pending, while a same-session record that omits the background
	key entirely rebuilds into _pending - same (conv_id, cli_session_id) key,
	different maps, so both survive hydration without colliding."""
	registry = Registry()
	logger = make_logger()
	snapshot = {
		"conversations": {
			"conv-1": _conv_with_pending({
				"req-bg": {
					"sender": "Claude", "questionText": "Also noting: cache warmed", "cancelled": False,
					"msgId": "m-bg", "suggestions": None,
					"cliSessionId": "sess-abc", "askedAt": "2026-07-07T10:00:00+00:00",
					"background": True,
				},
				"req-blocking": {
					"sender": "Claude", "questionText": "Deploy?", "cancelled": False,
					"msgId": "m-q", "suggestions": None,
					"cliSessionId": "sess-abc", "askedAt": "2026-07-07T10:05:00+00:00",
				},
			}),
		},
	}
	with patch("server.hydration.db", make_firebase_db_mock(snapshot)):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry.pending_count == 2

	bg_rec = registry.find_by_request_id("conv-1", "req-bg")
	assert bg_rec is not None
	assert bg_rec.background is True
	assert bg_rec.future is None
	assert bg_rec.cli_session_id == "sess-abc"

	parked_rec = registry.find_by_request_id("conv-1", "req-blocking")
	assert parked_rec is not None
	assert parked_rec.background is False
	assert parked_rec.future is None
	assert parked_rec.cli_session_id == "sess-abc"
