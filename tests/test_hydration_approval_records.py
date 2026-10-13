"""Hydration cancels an approval card's pending_questions record instead of
parking it: the hook connection it answered died with the restart."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.registry import Registry
from tests.test_hydration import conv_snapshot, make_firebase_db_mock, make_logger, member_data


@pytest.mark.asyncio
async def test_hydration_cancels_an_approval_card_instead_of_parking_it():
	registry = Registry()
	logger = make_logger()
	backend = MagicMock()
	backend.mark_question_cancelled = AsyncMock()
	node = conv_snapshot(
		"conv-1", members={"Claude": member_data(cli_session_id="sess-abc", sender="Claude", alive=True)},
	)
	node["pending_questions"] = {
		"apr-1": {
			"sender": "Claude", "questionText": "Approve Bash: ls", "cancelled": False, "msgId": "m-1",
			"suggestions": ["Allow", "Deny"], "cliSessionId": "sess-abc",
			"askedAt": "2026-10-12T10:00:00+00:00", "approval": True,
		},
	}
	with patch("server.hydration.db", make_firebase_db_mock({"conversations": {"conv-1": node}})):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, backend, logger)
	assert registry.pending_count == 0
	backend.mark_question_cancelled.assert_awaited_once_with("conv-1", "apr-1")
