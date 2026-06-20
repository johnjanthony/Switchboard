"""P1-5 (M06): the startup away-mode reset must also clear the
away_mode_commands node, so a stale enter_global from before the restart
cannot replay from the command listener's initial snapshot and silently
re-enable away mode after the reset."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_reset_clears_away_mode_commands(monkeypatch):
	from server import firebase as fb_module
	refs: dict[str, MagicMock] = {}

	def reference(path: str):
		return refs.setdefault(path, MagicMock())

	mock_db = MagicMock()
	mock_db.reference = reference
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)

	await be.reset_all_away_mode()

	refs["global_settings/away_mode"].set.assert_called_once_with(False)
	refs["away_mode_commands"].delete.assert_called_once_with()
