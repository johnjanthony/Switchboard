"""Firebase hidden-aware FCM dispatch tests.

Exercises the hide-gating logic in `FirebaseBackend.write_channel_message`
via an in-test subclass that injects a fake `hidden` reader and records
whether the FCM send path fired.
"""

from __future__ import annotations

import asyncio
import pytest

from server.firebase import FirebaseBackend


class _FakeBackend(FirebaseBackend):
	"""Subclass that bypasses __init__ and stubs the Firebase calls we don't want
	hitting the network."""

	def __init__(self, hidden_by_channel: dict[str, bool]) -> None:
		# Skip FirebaseBackend.__init__ (Firebase admin SDK) entirely.
		self._hidden_by_channel = dict(hidden_by_channel)
		self._fcm_sent: list[str] = []
		self._hidden_writes: list[tuple[str, bool]] = []
		self._messages_written: list[tuple[str, str, str]] = []
		self._loop = asyncio.get_running_loop()
		self._storage_bucket = None
		self._logger = None

	async def _read_hidden(self, channel_id: str) -> bool:
		return self._hidden_by_channel.get(channel_id, False)

	async def _write_hidden(self, channel_id: str, value: bool) -> None:
		self._hidden_by_channel[channel_id] = value
		self._hidden_writes.append((channel_id, value))

	async def _write_message_node(self, channel_id: str, msg_id: str, data: dict) -> None:
		self._messages_written.append((channel_id, msg_id, data["message_type"]))

	async def _send_fcm(
		self, channel_id: str, message_type: str, sender: str, content: str, data: dict
	) -> None:
		self._fcm_sent.append(f"{channel_id}:{message_type}")

	async def _write_default_meta_if_missing(self, channel_id: str) -> None:
		pass


@pytest.mark.asyncio
async def test_hidden_channel_notify_skips_fcm():
	backend = _FakeBackend({"c1": True})
	await backend.write_channel_message("c1", "Claude", "notify", "hi")
	assert backend._fcm_sent == []
	assert backend._messages_written and backend._messages_written[0][2] == "notify"
	assert backend._hidden_writes == []  # did not unhide


@pytest.mark.asyncio
async def test_hidden_channel_document_skips_fcm():
	backend = _FakeBackend({"c1": True})
	await backend.write_channel_message(
		"c1", "Claude", "document", "report.txt", url="https://example/r"
	)
	assert backend._fcm_sent == []


@pytest.mark.asyncio
async def test_hidden_channel_question_unhides_and_sends_fcm():
	backend = _FakeBackend({"c1": True})
	await backend.write_channel_message(
		"c1", "Claude", "question", "Proceed?", request_id="req-1"
	)
	assert backend._hidden_writes == [("c1", False)]
	assert backend._fcm_sent == ["c1:question"]
	# unhide must land BEFORE the FCM push so Android never displays
	# a question notification for a channel it still thinks is hidden.


@pytest.mark.asyncio
async def test_visible_channel_fires_fcm_and_no_hidden_write():
	backend = _FakeBackend({"c1": False})
	await backend.write_channel_message("c1", "Claude", "notify", "status")
	assert backend._fcm_sent == ["c1:notify"]
	assert backend._hidden_writes == []


@pytest.mark.asyncio
async def test_absent_hidden_treated_as_visible():
	backend = _FakeBackend({})  # no entry for "c1"
	await backend.write_channel_message("c1", "Claude", "notify", "status")
	assert backend._fcm_sent == ["c1:notify"]
	assert backend._hidden_writes == []


class _MirrorFakeBackend(FirebaseBackend):
	def __init__(self) -> None:
		self._mirror_writes: list[tuple[bool, int]] = []
		self._loop = asyncio.get_running_loop()
		self._logger = None
		self._storage_bucket = None

	async def _write_away_mode_node(self, active: bool, updated_at: int) -> None:
		self._mirror_writes.append((active, updated_at))


@pytest.mark.asyncio
async def test_write_away_mode_mirror_writes_node():
	backend = _MirrorFakeBackend()
	await backend.write_away_mode_mirror(True)
	assert len(backend._mirror_writes) == 1
	active, ts = backend._mirror_writes[0]
	assert active is True
	assert ts > 0


@pytest.mark.asyncio
async def test_write_away_mode_mirror_accepts_false():
	backend = _MirrorFakeBackend()
	await backend.write_away_mode_mirror(False)
	assert backend._mirror_writes[0][0] is False
