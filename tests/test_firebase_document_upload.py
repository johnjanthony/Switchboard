"""B3: a document-upload failure in FirebaseBackend.write_conversation_message
must fail loudly (re-raise) so send_document_human returns ERROR, instead of
silently writing the local filesystem path as the message url and telling the
agent 'ok' (the phone cannot fetch a local path)."""
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


@pytest.mark.asyncio
async def test_document_upload_failure_reraises(monkeypatch):
	"""No storage bucket configured -> _upload_file raises -> the write must
	propagate the failure rather than swallowing it and writing a local path."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket=None)

	with pytest.raises(Exception):
		await be.write_conversation_message(
			"conv-1", "Claude", "document", "my caption",
			url="C:/tmp/doc.pdf", filename="doc.pdf",
		)


@pytest.mark.asyncio
async def test_document_with_http_url_does_not_upload_or_raise(monkeypatch):
	"""Scoping guard: an already-uploaded (http) url skips _upload_file entirely,
	so the re-raise only affects genuine local-path upload failures."""
	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket=None)

	async def _noop_fcm(*a, **k):
		return None

	be._send_fcm = _noop_fcm

	corr, _msg_id = await be.write_conversation_message(
		"conv-1", "Claude", "document", "cap",
		url="https://example.com/x.pdf", filename="x.pdf",
	)
	assert corr == ("conv-1", "Claude")


@pytest.mark.asyncio
async def test_document_upload_stores_storage_path(monkeypatch):
	"""When the server uploads a local file, the written message must carry the
	storage_path blob name so the /document proxy can re-download it later."""
	import asyncio as _asyncio
	loop = _asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")

	# Mock firebase storage so _upload_file returns a signed url and we control the blob.
	from server import firebase as fb_module

	class _FakeBlob:
		def __init__(self, name):
			self.name = name
		def upload_from_filename(self, _path):
			return None
		def generate_signed_url(self, **_kw):
			return "https://storage.googleapis.com/my-bucket/" + self.name

	class _FakeBucket:
		def blob(self, name):
			return _FakeBlob(name)

	fake_storage = MagicMock()
	fake_storage.bucket.return_value = _FakeBucket()
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	async def _noop_fcm(*a, **k):
		return None
	be._send_fcm = _noop_fcm

	await be.write_conversation_message(
		"conv-1", "Claude", "document", "cap",
		url="C:/tmp/report.md", filename="report.md",
	)

	# The message push payload is the arg to db.reference(...).push().set(payload).
	set_mock = fb_module.db.reference.return_value.push.return_value.set
	payload = set_mock.call_args[0][0]
	assert payload["type"] == "document"
	assert payload["storage_path"].startswith("documents/")
	assert payload["storage_path"].endswith("/report.md")


@pytest.mark.asyncio
async def test_read_document_downloads_by_storage_path(monkeypatch):
	import asyncio as _asyncio
	loop = _asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module

	fb_module.db.reference.return_value.get.return_value = {
		"type": "document", "filename": "report.md",
		"storage_path": "documents/abc/report.md",
	}

	class _FakeBlob:
		size = 7
		def reload(self):
			return None
		def download_as_bytes(self):
			return b"# Hello"
	class _FakeBucket:
		def blob(self, name):
			assert name == "documents/abc/report.md"
			return _FakeBlob()
	fake_storage = MagicMock()
	fake_storage.bucket.return_value = _FakeBucket()
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	data, filename = await be.read_document("conv-1", "m-1")
	assert data == b"# Hello"
	assert filename == "report.md"


@pytest.mark.asyncio
async def test_read_document_falls_back_to_url_blob_path(monkeypatch):
	import asyncio as _asyncio
	loop = _asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module

	# No storage_path (old message) — derive it from the stored signed url.
	fb_module.db.reference.return_value.get.return_value = {
		"type": "document", "filename": "old.md",
		"url": "https://storage.googleapis.com/my-bucket/documents/xyz/old.md?X-Goog-Signature=z",
	}

	class _FakeBlob:
		size = 7
		def reload(self):
			return None
		def download_as_bytes(self):
			return b"old body"
	class _FakeBucket:
		def blob(self, name):
			assert name == "documents/xyz/old.md"
			return _FakeBlob()
	fake_storage = MagicMock()
	fake_storage.bucket.return_value = _FakeBucket()
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	data, filename = await be.read_document("conv-1", "m-2")
	assert data == b"old body"
	assert filename == "old.md"


@pytest.mark.asyncio
async def test_read_document_missing_message_raises_lookuperror(monkeypatch):
	import asyncio as _asyncio
	loop = _asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module
	fb_module.db.reference.return_value.get.return_value = None
	with pytest.raises(LookupError):
		await be.read_document("conv-1", "missing")


@pytest.mark.asyncio
async def test_read_document_non_document_raises_lookuperror(monkeypatch):
	import asyncio as _asyncio
	loop = _asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module
	fb_module.db.reference.return_value.get.return_value = {"type": "notify", "text": "hi"}
	with pytest.raises(LookupError):
		await be.read_document("conv-1", "m-3")


@pytest.mark.asyncio
async def test_read_document_rejects_oversized_blob(monkeypatch):
	import asyncio as _asyncio
	loop = _asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop, bucket="my-bucket")
	from server import firebase as fb_module
	from server.gateway.document import _MAX_DOCUMENT_BYTES

	fb_module.db.reference.return_value.get.return_value = {
		"type": "document", "filename": "big.bin",
		"storage_path": "documents/abc/big.bin",
	}

	class _BigBlob:
		size = _MAX_DOCUMENT_BYTES + 1
		def reload(self):
			return None
		def download_as_bytes(self):
			raise AssertionError("should not download an oversized blob")
	class _FakeBucket:
		def blob(self, name):
			return _BigBlob()
	fake_storage = MagicMock()
	fake_storage.bucket.return_value = _FakeBucket()
	monkeypatch.setattr(fb_module, "storage", fake_storage)

	with pytest.raises(ValueError):
		await be.read_document("conv-1", "m-big")
