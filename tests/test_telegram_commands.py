"""Tests for TelegramBackend command queue and spawn send methods."""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest
import respx

from server.telegram import TelegramBackend

BASE = "https://api.telegram.org/bottok"
CHAT_ID = "123"


@pytest.fixture
async def backend():
	async with httpx.AsyncClient() as client:
		yield TelegramBackend(token="tok", chat_id=CHAT_ID, http_client=client)


# --- poll_commands ---

@pytest.mark.asyncio
async def test_poll_commands_yields_item_from_queue(backend):
	await backend._command_queue.put("/spawn rpdm/next-gen do stuff")
	gen = backend.poll_commands()
	cmd = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
	await gen.aclose()
	assert cmd == "/spawn rpdm/next-gen do stuff"


@pytest.mark.asyncio
async def test_poll_commands_yields_bare_spawn(backend):
	await backend._command_queue.put("/spawn")
	gen = backend.poll_commands()
	cmd = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
	await gen.aclose()
	assert cmd == "/spawn"


# --- getUpdates loop routes /spawn to queue ---

@respx.mock
@pytest.mark.asyncio
async def test_spawn_command_enqueued_for_matching_chat_id(backend):
	call_count = 0

	async def fake_get(request):
		nonlocal call_count
		call_count += 1
		if call_count == 1:
			return httpx.Response(200, json={
				"ok": True,
				"result": [{
					"update_id": 1,
					"message": {
						"message_id": 10,
						"chat": {"id": 123},
						"text": "/spawn rpdm/next-gen do stuff",
					},
				}],
			})
		await asyncio.sleep(60)

	respx.get(f"{BASE}/getUpdates").mock(side_effect=fake_get)

	async def drain():
		async for _ in backend.poll_responses():
			pass

	task = asyncio.create_task(drain())
	try:
		cmd = await asyncio.wait_for(backend._command_queue.get(), timeout=2.0)
	finally:
		task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await task

	assert cmd == "/spawn rpdm/next-gen do stuff"


@respx.mock
@pytest.mark.asyncio
async def test_spawn_command_silently_dropped_for_wrong_chat_id(backend):
	call_count = 0

	async def fake_get(request):
		nonlocal call_count
		call_count += 1
		if call_count == 1:
			return httpx.Response(200, json={
				"ok": True,
				"result": [{
					"update_id": 1,
					"message": {
						"message_id": 10,
						"chat": {"id": 999},
						"text": "/spawn rpdm/next-gen do stuff",
					},
				}],
			})
		await asyncio.sleep(60)

	respx.get(f"{BASE}/getUpdates").mock(side_effect=fake_get)

	async def drain():
		async for _ in backend.poll_responses():
			pass

	task = asyncio.create_task(drain())
	try:
		with pytest.raises(asyncio.TimeoutError):
			await asyncio.wait_for(backend._command_queue.get(), timeout=0.5)
	finally:
		task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await task


@respx.mock
@pytest.mark.asyncio
async def test_reply_message_still_yields_from_poll_responses(backend):
	call_count = 0

	async def fake_get(request):
		nonlocal call_count
		call_count += 1
		if call_count == 1:
			return httpx.Response(200, json={
				"ok": True,
				"result": [{
					"update_id": 1,
					"message": {
						"message_id": 20,
						"chat": {"id": 123},
						"text": "yes",
						"reply_to_message": {"message_id": 5},
					},
				}],
			})
		await asyncio.sleep(60)

	respx.get(f"{BASE}/getUpdates").mock(side_effect=fake_get)

	received = []

	async def collect_one():
		async for resp in backend.poll_responses():
			received.append(resp)
			return

	task = asyncio.create_task(collect_one())
	try:
		await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
	except asyncio.TimeoutError:
		pass
	finally:
		task.cancel()
		with contextlib.suppress(asyncio.CancelledError):
			await task

	assert len(received) == 1
	assert received[0].correlation == 5
	assert received[0].text == "yes"


# --- send_spawn_ack ---

@respx.mock
@pytest.mark.asyncio
async def test_send_spawn_ack_with_prompt(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
	)
	await backend.send_spawn_ack("rpdm/next-gen", "fix migration")
	body = route.calls.last.request.read().decode()
	assert "rpdm/next-gen" in body
	assert "fix migration" in body
	assert "Windows Terminal" in body


@respx.mock
@pytest.mark.asyncio
async def test_send_spawn_ack_without_prompt_uses_ask_variant(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
	)
	await backend.send_spawn_ack("rpdm/next-gen", None)
	body = route.calls.last.request.read().decode()
	assert "rpdm/next-gen" in body
	assert "ask" in body
	assert "Windows Terminal" in body


# --- send_text ---

@respx.mock
@pytest.mark.asyncio
async def test_send_text_posts_plain_message(backend):
	route = respx.post(f"{BASE}/sendMessage").mock(
		return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
	)
	await backend.send_text("Spawn not configured.")
	body = route.calls.last.request.read().decode()
	assert "Spawn not configured." in body
	assert CHAT_ID in body
