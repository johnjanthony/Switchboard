"""The retention sweep's delete_conversation_nodes must delete the
Storage blobs referenced by a conversation's message nodes (read-before-delete)
before tearing down the RTDB nodes, and must never let a blob-delete failure
strand the RTDB teardown."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def _make_backend(monkeypatch, loop, bucket=None):
	from server import firebase as fb_module
	monkeypatch.setattr(fb_module, "db", MagicMock())
	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._supervised = {}
	be._logger = None
	be._loop = loop
	be._storage_bucket = bucket
	return be


def test_storage_paths_in_messages_collects_only_nonempty_string_paths():
	from server.firebase import _storage_paths_in_messages
	msgs = {
		"m1": {"storage_path": "documents/a/x.md"},
		"m2": {"type": "notify"},
		"m3": {"storage_path": ""},
		"m4": "not-a-dict",
		"m5": {"storage_path": "documents/b/y.pdf"},
	}
	assert sorted(_storage_paths_in_messages(msgs)) == ["documents/a/x.md", "documents/b/y.pdf"]


def test_storage_paths_in_messages_tolerates_none_and_garbage():
	from server.firebase import _storage_paths_in_messages
	assert _storage_paths_in_messages(None) == []
	assert _storage_paths_in_messages({}) == []
	assert _storage_paths_in_messages("garbage") == []


@pytest.mark.asyncio
async def test_delete_conversation_nodes_deletes_blobs_then_rtdb(monkeypatch):
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module
	fb_module.db.reference.return_value.get.return_value = {
		"m1": {"type": "document", "storage_path": "documents/a/one.md"},
		"m2": {"type": "notify", "text": "hi"},
		"m3": {"type": "document", "storage_path": "documents/b/two.pdf"},
	}
	deleted_blobs = []

	class _FakeBlob:
		def __init__(self, name):
			self.name = name
		def delete(self):
			deleted_blobs.append(self.name)

	class _FakeBucket:
		def blob(self, name):
			return _FakeBlob(name)

	fake_storage = MagicMock()
	fake_storage.bucket.return_value = _FakeBucket()
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	await be.delete_conversation_nodes("conv-1")

	assert sorted(deleted_blobs) == ["documents/a/one.md", "documents/b/two.pdf"]
	fb_module.db.reference.return_value.update.assert_called_once()
	arg = fb_module.db.reference.return_value.update.call_args[0][0]
	assert arg == {"conversations/conv-1": None, "messages/conv-1": None, "answers/conv-1": None}


@pytest.mark.asyncio
async def test_delete_conversation_nodes_survives_blob_failure(monkeypatch):
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module
	fb_module.db.reference.return_value.get.return_value = {
		"m1": {"type": "document", "storage_path": "documents/a/one.md"},
		"m2": {"type": "document", "storage_path": "documents/b/two.pdf"},
	}
	deleted = []

	class _FakeBlob:
		def __init__(self, name):
			self.name = name
		def delete(self):
			if self.name == "documents/a/one.md":
				raise RuntimeError("boom")
			deleted.append(self.name)

	class _FakeBucket:
		def blob(self, name):
			return _FakeBlob(name)

	fake_storage = MagicMock()
	fake_storage.bucket.return_value = _FakeBucket()
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	await be.delete_conversation_nodes("conv-1")  # must NOT raise

	assert deleted == ["documents/b/two.pdf"]
	fb_module.db.reference.return_value.update.assert_called_once()


@pytest.mark.asyncio
async def test_delete_conversation_nodes_skips_blobs_when_bucket_unset(monkeypatch):
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket=None)
	from server import firebase as fb_module
	fake_storage = MagicMock()
	fake_storage.bucket.side_effect = AssertionError("must not touch storage when bucket unset")
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	await be.delete_conversation_nodes("conv-1")  # no raise
	fb_module.db.reference.return_value.update.assert_called_once()


@pytest.mark.asyncio
async def test_delete_conversation_nodes_survives_read_failure(monkeypatch):
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module
	fb_module.db.reference.return_value.get.side_effect = RuntimeError("boom")

	await be.delete_conversation_nodes("conv-1")  # must NOT raise

	fb_module.db.reference.return_value.update.assert_called_once()
