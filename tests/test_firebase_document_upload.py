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
