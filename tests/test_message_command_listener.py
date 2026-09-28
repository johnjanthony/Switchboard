"""The message_commands listener hands the entry's RTDB push key to its
handler; every other command listener keeps calling handler(cmd) alone."""
import asyncio
import contextlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


class _Event:
	def __init__(self, event_type, path, data):
		self.event_type = event_type
		self.path = path
		self.data = data


def _make_backend(monkeypatch, loop):
	from server import firebase as fb_module
	import server.firebase_supervisor as fbsup_module

	monkeypatch.setattr(fb_module, "db", MagicMock())
	monkeypatch.setattr(fbsup_module, "db", MagicMock())

	be = fb_module.FirebaseBackend.__new__(fb_module.FirebaseBackend)
	be._loop = loop
	be._supervised = {}
	be._logger = None
	be.send_text = AsyncMock()
	return be


async def _pump():
	for _ in range(10):
		await asyncio.sleep(0)


async def _cleanup(be):
	for sup in be._supervised.values():
		with contextlib.suppress(Exception):
			await sup.stop()


def _entry():
	return {
		"conversation_id": "conv-1", "text": "hi",
		"issued_at": datetime.now(timezone.utc).isoformat(),
	}


@pytest.mark.asyncio
async def test_message_listener_passes_the_command_key(monkeypatch):
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	seen = []

	async def handler(cmd, command_id=None):
		seen.append((cmd, command_id))

	await be.start_message_command_listener(handler)
	entry = _entry()
	be._supervised["message_commands"]._user_callback(_Event("put", "/cmd-abc", entry))
	await _pump()

	assert seen == [(entry, "cmd-abc")]
	await _cleanup(be)


@pytest.mark.asyncio
async def test_other_listeners_call_handler_with_the_command_only(monkeypatch):
	"""A positional-only handler raises TypeError on an unexpected command_id
	keyword; the task logs it and `seen` stays empty, so this discriminates."""
	be = _make_backend(monkeypatch, asyncio.get_running_loop())
	seen = []

	async def handler(cmd):
		seen.append(cmd)

	await be.start_force_end_command_listener(handler)
	entry = {"conversation_id": "conv-1", "issued_at": datetime.now(timezone.utc).isoformat()}
	be._supervised["force_end_commands"]._user_callback(_Event("put", "/cmd-xyz", entry))
	await _pump()

	assert seen == [entry]
	await _cleanup(be)
