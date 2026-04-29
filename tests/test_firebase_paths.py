"""Tests for Firebase path layout under the new schema."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def backend(monkeypatch):
	from server import firebase as fb_module
	mock_db = MagicMock()
	monkeypatch.setattr(fb_module, "db", mock_db)
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._logger = None
	return be, mock_db


@pytest.mark.asyncio
async def test_global_away_mirror_writes_new_path(backend):
	be, mock_db = backend
	await be.write_away_mode_mirror(None, True)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("global_settings/away_mode" in c for c in calls)
	assert not any("away_mode/global" in c for c in calls)


@pytest.mark.asyncio
async def test_per_cwd_override_writes_channel_field(backend):
	be, mock_db = backend
	await be.write_away_mode_mirror("c:/work/sw", True)
	calls = [str(c) for c in mock_db.reference.call_args_list]
	assert any("channels/c:__work__sw/away_mode" in c for c in calls)
	assert not any("away_mode/overrides" in c for c in calls)


@pytest.mark.asyncio
async def test_per_cwd_override_remove_deletes_channel_field(backend):
	be, mock_db = backend
	await be.write_away_mode_mirror("c:/work/sw", None)
	# The reference call for the channel away_mode path was made; the .delete()
	# should have been called on the returned Reference mock.
	any_channel_ref_call = any(
		"channels/c:__work__sw/away_mode" in str(c)
		for c in mock_db.reference.call_args_list
	)
	assert any_channel_ref_call
	# .delete() was called at least once on the mock reference chain
	mock_db.reference.return_value.delete.assert_called()


@pytest.mark.asyncio
async def test_wipe_channel_deletes_pending_responses_and_away_mode(backend):
	be, mock_db = backend
	await be.wipe_channel("c:/work/sw")
	deleted_paths = [str(c) for c in mock_db.reference.call_args_list]
	assert any("channels/c:__work__sw/pending_responses" in p for p in deleted_paths)
	assert any("channels/c:__work__sw/away_mode" in p for p in deleted_paths)
