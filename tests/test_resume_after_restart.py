"""P0-2 acceptance: resume works after a server restart, and member state is
the single source of truth for resumability.

Regression for H03/M21: hydration used to re-bind dormant resumable members
into session_to_conversation_id while resume eligibility required them to be
unbound, so post-restart phone Resume silently did nothing, permanently."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from server.config import Config
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember, Registry
from server.spawn import SpawnHandler
from tests.test_hydration import (
	conv_snapshot,
	make_firebase_db_mock,
	make_logger,
	member_data,
)


def _spawn_handler(tmp_path: Path, registry: Registry) -> SpawnHandler:
	cfg = Config(
		host="127.0.0.1",
		port=9876,
		timeout_seconds=60,
		log_path=str(tmp_path / "log.jsonl"),
		windows_spawn_root=tmp_path,
	)
	return SpawnHandler(cfg, AsyncMock(), JsonlLogger(cfg.log_path), registry)


@pytest.mark.asyncio
async def test_resume_drift_dormant_but_bound_member_still_resumes_and_logs(tmp_path):
	"""Member state is the single source of truth for resumability. A dormant
	member that is unexpectedly still bound (drift) is resumed anyway, with a
	loud log, instead of silently disabling Resume."""
	registry = Registry()
	conv = Conversation(id="conv-drift", title="Drift")
	member = ConversationMember(
		cli_session_id="sess-d", sender="Claude", cwd="C:/Work/X",
		surface="windows", joined_at=0.0, alive=False,
	)
	conv.members_active["sess-d"] = member
	registry.conversations["conv-drift"] = conv
	registry.bind_session("sess-d", "conv-drift")  # the drift: dormant yet bound

	handler = _spawn_handler(tmp_path, registry)
	errors: list[str] = []

	async def _capture(msg, **kwargs):
		errors.append(msg)

	handler._logger.surface_error = _capture  # type: ignore[method-assign]

	with patch.object(handler, "_user_has_interactive_session", AsyncMock(return_value=True)), \
			patch.object(handler, "_invoke_launcher", AsyncMock()):
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-drift",
			"issued_at": "2026-06-11T00:00:00Z",
		})

	assert any("resume_eligibility_drift" in e for e in errors), \
		f"expected a drift log; got: {errors}"
	new_convs = [c for c in registry.conversations.values() if c.continued_from == "conv-drift"]
	assert len(new_convs) == 1, "drifted member must still be resumable (state wins)"


@pytest.mark.asyncio
async def test_resume_works_after_hydration_round_trip(tmp_path):
	"""P0-2 acceptance: a conversation with a dormant resumable member can
	still be resumed from the phone after a server restart (real hydration)."""
	registry = Registry()
	logger = make_logger()
	snapshot = {
		"conversations": {
			"conv-src": conv_snapshot(
				"conv-src",
				members={
					"Claude": member_data(
						cli_session_id="sess-dormant",
						sender="Claude",
						alive=False,
						session_lost_permanently=False,
					),
				},
			),
		},
	}
	with patch("server.hydration.db", make_firebase_db_mock(snapshot)):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	handler = _spawn_handler(tmp_path, registry)
	with patch.object(handler, "_user_has_interactive_session", AsyncMock(return_value=True)), \
			patch.object(handler, "_invoke_launcher", AsyncMock()) as mock_launch:
		await handler.handle_resume({
			"type": "resume",
			"source_conversation_id": "conv-src",
			"issued_at": "2026-06-11T00:00:00Z",
		})

	# A continuation conversation was minted from the source
	new_convs = [c for c in registry.conversations.values() if c.continued_from == "conv-src"]
	assert len(new_convs) == 1, "resume must mint a continuation conversation post-restart"
	new_conv = new_convs[0]
	# The dormant member moved, re-bound, flipped alive
	assert "sess-dormant" in new_conv.members_active
	assert new_conv.members_active["sess-dormant"].alive is True
	assert registry.session_to_conversation_id.get("sess-dormant") == new_conv.id
	# Spawn-pending file written and launcher fired
	pending = list(tmp_path.glob("spawn-pending-*.json"))
	assert len(pending) == 1
	payload = json.loads(pending[0].read_text(encoding="utf-8"))
	assert payload["type"] == "resume"
	assert payload["agents"][0]["cli_session_id"] == "sess-dormant"
	mock_launch.assert_awaited_once()
