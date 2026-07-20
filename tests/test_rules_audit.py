"""REV-004: the phone command channel (spawn/force-end/combine) rests on the
deployed RTDB rules being a real single-UID lock. classify_rtdb_rules flags
placeholder/test-mode/world-open rules; audit_rtdb_rules surfaces them loudly
at startup without ever blocking it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.rules_audit import audit_rtdb_rules, classify_rtdb_rules

_REAL_RULES = """
{
	"rules": {
		".read": "auth != null && auth.uid == 'abc123realuid'",
		".write": "auth != null && auth.uid == 'abc123realuid'"
	}
}
"""


def test_real_single_uid_rules_are_clean():
	assert classify_rtdb_rules(_REAL_RULES) == []


def test_placeholder_uid_is_flagged():
	text = _REAL_RULES.replace("abc123realuid", "YOUR_FIREBASE_UID")
	problems = classify_rtdb_rules(text)
	assert any("placeholder" in p for p in problems)


@pytest.mark.parametrize("verb", [".read", ".write"])
def test_literal_true_rule_is_flagged(verb):
	text = '{ "rules": { "%s": true } }' % verb
	problems = classify_rtdb_rules(text)
	assert any("literally true" in p for p in problems)


def test_test_mode_expiry_is_flagged():
	text = '{ "rules": { ".read": "now < 1767225600000", ".write": "now < 1767225600000" } }'
	problems = classify_rtdb_rules(text)
	assert any("test-mode" in p for p in problems)


def test_empty_rules_are_flagged():
	assert classify_rtdb_rules("") == ["deployed rules are empty"]


@pytest.mark.asyncio
async def test_audit_surfaces_problems_and_notifies():
	backend = MagicMock()
	backend.fetch_database_rules = AsyncMock(return_value='{ "rules": { ".read": true } }')
	backend.send_text = AsyncMock()
	logger = MagicMock()
	logger.surface_error = AsyncMock()

	await audit_rtdb_rules(backend, logger)

	logger.surface_error.assert_awaited_once()
	assert "rtdb_rules_audit_failed" in logger.surface_error.await_args.args[0]
	backend.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_clean_rules_stay_silent():
	backend = MagicMock()
	backend.fetch_database_rules = AsyncMock(return_value=_REAL_RULES)
	backend.send_text = AsyncMock()
	logger = MagicMock()
	logger.surface_error = AsyncMock()

	await audit_rtdb_rules(backend, logger)

	logger.surface_error.assert_not_awaited()
	backend.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_fetch_failure_is_nonfatal():
	backend = MagicMock()
	backend.fetch_database_rules = AsyncMock(side_effect=OSError("network down"))
	backend.send_text = AsyncMock()
	logger = MagicMock()
	logger.surface_error = AsyncMock()

	await audit_rtdb_rules(backend, logger)  # must not raise

	assert "rtdb_rules_audit_error" in logger.surface_error.await_args.args[0]
	backend.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_database_rules_hits_settings_endpoint(monkeypatch):
	import asyncio
	import io
	import urllib.request

	from tests.test_firebase_document_upload import _make_backend

	loop = asyncio.get_running_loop()
	be = _make_backend(monkeypatch, loop)
	be._database_url = "https://example-db.firebaseio.com"

	import server.firebase as fb_module
	fake_app = MagicMock()
	fake_app.credential.get_access_token.return_value = MagicMock(access_token="tok-xyz")
	monkeypatch.setattr(fb_module.firebase_admin, "get_app", lambda: fake_app)

	seen: dict = {}

	class _Resp(io.BytesIO):
		def __enter__(self):
			return self

		def __exit__(self, *a):
			return False

	def fake_urlopen(url, timeout=None):
		seen["url"] = url
		seen["timeout"] = timeout
		return _Resp(b'{ "rules": {} }')

	monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

	text = await be.fetch_database_rules()

	assert text == '{ "rules": {} }'
	assert seen["url"] == "https://example-db.firebaseio.com/.settings/rules.json?access_token=tok-xyz"
	assert seen["timeout"] == 10
