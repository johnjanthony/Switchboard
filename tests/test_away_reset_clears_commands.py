"""M06 + T-029: the startup command-clear wipes the away_mode_commands node so
a stale toggle from before the restart cannot replay from the command
listener's initial snapshot. It must NOT touch the away flag - away mode now
persists across restart (T-029)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_clear_wipes_away_mode_commands_without_touching_flag(monkeypatch):
	from server import firebase as fb_module
	refs: dict[str, MagicMock] = {}

	def reference(path: str):
		return refs.setdefault(path, MagicMock())

	mock_db = MagicMock()
	mock_db.reference = reference
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)

	await be.clear_pending_away_mode_commands()

	refs["away_mode_commands"].delete.assert_called_once_with()
	# away mode persists across restart (T-029): the flag must not be written on startup.
	assert "global_settings/away_mode" not in refs
