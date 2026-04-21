"""Tests for the Telegram backend's poll_responses async generator."""

import asyncio

import httpx
import pytest
import respx

from server.messenger import IncomingResponse
from server.telegram import TelegramBackend

BASE = "https://api.telegram.org/bottok"


def _cq_update(update_id: int, message_id: int, data: str, chat_id: str = "123") -> dict:
	return {
		"update_id": update_id,
		"callback_query": {
			"id": f"cq_{update_id}",
			"data": data,
			"message": {
				"message_id": message_id,
				"chat": {"id": int(chat_id)},
			},
		},
	}


def _update(update_id: int, reply_to: int | None, text: str) -> dict:
	msg = {"message_id": update_id + 1000, "text": text}
	if reply_to is not None:
		msg["reply_to_message"] = {"message_id": reply_to}
	return {"update_id": update_id, "message": msg}


@pytest.fixture
async def backend():
	async with httpx.AsyncClient() as client:
		yield TelegramBackend(token="tok", chat_id="123", http_client=client)


@respx.mock
@pytest.mark.asyncio
async def test_yields_incoming_response_for_reply(backend):
	respx.get(f"{BASE}/getUpdates").mock(
		return_value=httpx.Response(
			200,
			json={"ok": True, "result": [_update(1, reply_to=777, text="yes")]},
		)
	)
	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert isinstance(response, IncomingResponse)
	assert response.correlation == 777
	assert response.text == "yes"


@respx.mock
@pytest.mark.asyncio
async def test_skips_updates_without_reply_to(backend):
	respx.get(f"{BASE}/getUpdates").mock(
		side_effect=[
			httpx.Response(
				200,
				json={
					"ok": True,
					"result": [
						_update(1, reply_to=None, text="hello"),
						_update(2, reply_to=777, text="yes"),
					],
				},
			),
		]
	)
	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert response.correlation == 777


@respx.mock
@pytest.mark.asyncio
async def test_advances_offset_between_polls(backend):
	first = httpx.Response(
		200,
		json={"ok": True, "result": [_update(5, reply_to=777, text="a")]},
	)
	second = httpx.Response(
		200,
		json={"ok": True, "result": [_update(9, reply_to=888, text="b")]},
	)
	route = respx.get(f"{BASE}/getUpdates").mock(side_effect=[first, second])
	agen = backend.poll_responses()
	r1 = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	r2 = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert r1.text == "a"
	assert r2.text == "b"
	# Second call must include offset=6 (5+1).
	second_url = str(route.calls[1].request.url)
	assert "offset=6" in second_url


@respx.mock
@pytest.mark.asyncio
async def test_poll_handles_429_with_retry_after(backend, monkeypatch):
	sleeps: list[float] = []
	async def fake_sleep(seconds):
		sleeps.append(seconds)
	monkeypatch.setattr(asyncio, "sleep", fake_sleep)

	first = httpx.Response(
		429, json={"ok": False, "parameters": {"retry_after": 7}}
	)
	second = httpx.Response(
		200,
		json={"ok": True, "result": [_update(1, reply_to=777, text="yes")]},
	)
	respx.get(f"{BASE}/getUpdates").mock(side_effect=[first, second])

	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert response.correlation == 777
	assert 7 in sleeps


@respx.mock
@pytest.mark.asyncio
async def test_poll_yields_callback_query_as_response(backend):
	respx.post(f"{BASE}/answerCallbackQuery").mock(
		return_value=httpx.Response(200, json={"ok": True, "result": True})
	)
	respx.get(f"{BASE}/getUpdates").mock(
		return_value=httpx.Response(
			200,
			json={"ok": True, "result": [_cq_update(10, message_id=777, data="yes")]},
		)
	)
	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert response.correlation == 777
	assert response.text == "yes"


@respx.mock
@pytest.mark.asyncio
async def test_poll_calls_answer_callback_query_on_tap(backend):
	ack_route = respx.post(f"{BASE}/answerCallbackQuery").mock(
		return_value=httpx.Response(200, json={"ok": True, "result": True})
	)
	respx.get(f"{BASE}/getUpdates").mock(
		return_value=httpx.Response(
			200,
			json={"ok": True, "result": [_cq_update(11, message_id=888, data="no")]},
		)
	)
	agen = backend.poll_responses()
	await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	assert ack_route.called
	ack_body = ack_route.calls.last.request.read().decode()
	assert "cq_11" in ack_body


@respx.mock
@pytest.mark.asyncio
async def test_poll_ignores_callback_query_from_wrong_chat(backend):
	respx.get(f"{BASE}/getUpdates").mock(
		side_effect=[
			httpx.Response(
				200,
				json={
					"ok": True,
					"result": [
						_cq_update(12, message_id=999, data="yes", chat_id="999"),
						_cq_update(13, message_id=777, data="no", chat_id="123"),
					],
				},
			),
		]
	)
	respx.post(f"{BASE}/answerCallbackQuery").mock(
		return_value=httpx.Response(200, json={"ok": True, "result": True})
	)
	agen = backend.poll_responses()
	response = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
	# Only the message from chat_id=123 should be yielded.
	assert response.correlation == 777
	assert response.text == "no"


@respx.mock
@pytest.mark.asyncio
async def test_poll_logs_surface_error_on_http_error(tmp_path, monkeypatch):
	from server.logging_jsonl import JsonlLogger
	log_path = tmp_path / "log.jsonl"
	logger = JsonlLogger(log_path)

	# Build a backend that carries the logger.
	async with httpx.AsyncClient() as client:
		b = TelegramBackend(
			token="tok", chat_id="123", http_client=client, logger=logger
		)
		sleeps: list[float] = []
		async def fake_sleep(seconds):
			sleeps.append(seconds)
			# After one sleep, simulate cancellation so the generator exits cleanly.
			raise asyncio.CancelledError
		monkeypatch.setattr(asyncio, "sleep", fake_sleep)

		respx.get(f"{BASE}/getUpdates").mock(
			return_value=httpx.Response(500, text="boom")
		)
		agen = b.poll_responses()
		with pytest.raises(asyncio.CancelledError):
			await agen.__anext__()

	log_text = log_path.read_text()
	assert "telegram_poll_error" in log_text
	assert "tok" not in log_text
