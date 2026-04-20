"""Tests for the Telegram backend's outbound send methods."""

import httpx
import pytest
import respx

from server.telegram import TelegramBackend

BASE = "https://api.telegram.org/bottok"
CHAT_ID = "123"


@pytest.fixture
async def backend():
	async with httpx.AsyncClient() as client:
		yield TelegramBackend(
			token="tok", chat_id=CHAT_ID, http_client=client
		)


@respx.mock
@pytest.mark.asyncio
async def test_send_question_posts_sendmessage_and_returns_message_id(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 777}}
		)
	)
	correlation = await backend.send_question(
		"a3f1", "IR2", "Overwrite foo.java?"
	)
	assert correlation == 777
	assert route.called
	body = route.calls.last.request.read().decode()
	assert "Overwrite foo.java?" in body
	assert "IR2" in body
	assert "a3f1" in body
	assert CHAT_ID in body
	assert "force_reply" in body


@respx.mock
@pytest.mark.asyncio
async def test_send_question_raises_telegram_error_with_redacted_token(backend):
	respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(500, text="boom")
	)
	from server.telegram import TelegramError
	with pytest.raises(TelegramError) as excinfo:
		await backend.send_question("a3f1", "IR2", "q")
	assert "tok" not in str(excinfo.value)
	assert "<REDACTED>" in str(excinfo.value)


@respx.mock
@pytest.mark.asyncio
async def test_send_notification_posts_with_info_prefix(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 1}}
		)
	)
	await backend.send_notification("IR2", "starting migration")
	assert route.called
	body = route.calls.last.request.read().decode()
	assert "IR2" in body
	assert "starting migration" in body


@respx.mock
@pytest.mark.asyncio
async def test_send_timeout_followup_uses_reply_to(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 2}}
		)
	)
	await backend.send_timeout_followup(
		"a3f1", "IR2", timeout_seconds=86400, correlation=777
	)
	assert route.called
	body = route.calls.last.request.read().decode()
	assert '"reply_to_message_id": 777' in body or '"reply_to_message_id":777' in body
	assert "24h" in body


@respx.mock
@pytest.mark.asyncio
async def test_send_resolution_confirmation_uses_reply_to(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(
			200, json={"ok": True, "result": {"message_id": 3}}
		)
	)
	await backend.send_resolution_confirmation(
		"a3f1", "IR2", correlation=777
	)
	assert route.called
	body = route.calls.last.request.read().decode()
	assert '"reply_to_message_id": 777' in body or '"reply_to_message_id":777' in body
	assert "answered" in body


@respx.mock
@pytest.mark.asyncio
async def test_preflight_succeeds_on_valid_token(backend):
	respx.get(f"{BASE}/getMe").mock(
		return_value=httpx.Response(
			200,
			json={"ok": True, "result": {"id": 42, "username": "switchboard_bot"}},
		)
	)
	await backend.preflight()  # should not raise


@respx.mock
@pytest.mark.asyncio
async def test_preflight_raises_telegram_error_with_redacted_token(backend):
	respx.get(f"{BASE}/getMe").mock(
		return_value=httpx.Response(
			401, json={"ok": False, "description": "Unauthorized"}
		)
	)
	from server.telegram import TelegramError
	with pytest.raises(TelegramError) as excinfo:
		await backend.preflight()
	assert "tok" not in str(excinfo.value)
	assert "<REDACTED>" in str(excinfo.value)
