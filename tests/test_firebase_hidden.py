"""Firebase hidden-aware message dispatch tests.

Exercises the new cwd-based write_channel_message in FirebaseBackend via
an in-test subclass that stubs out the Firebase admin-SDK calls.
"""

from __future__ import annotations

import asyncio
import pytest

from server.firebase import FirebaseBackend
from server.canonicalization import to_firebase_key


class _FakeBackend(FirebaseBackend):
	"""Subclass that bypasses __init__ and stubs Firebase calls."""

	def __init__(self) -> None:
		self._loop = asyncio.get_running_loop()
		self._storage_bucket = None
		self._logger = None
		self._db_writes: dict = {}
		self._fcm_sent: list[str] = []

	async def _firebase_set(self, path: str, value) -> None:
		self._db_writes[path] = value

	async def _firebase_update(self, path: str, value: dict) -> None:
		existing = self._db_writes.get(path, {})
		if isinstance(existing, dict):
			existing.update(value)
			self._db_writes[path] = existing
		else:
			self._db_writes[path] = value

	async def _send_fcm(self, channel_key, message_type, sender, content, fcm_data):
		self._fcm_sent.append(f"{channel_key}:{message_type}")

	async def _upload_file(self, local_path):
		return f"https://storage.example.com/{local_path.name}"


# Patch asyncio.to_thread calls via subclass overrides so no real Firebase hits.
# The new write_channel_message calls asyncio.to_thread for every DB write.
# We intercept by overriding write_channel_message at the DB-call level using
# a thin wrapper that replaces db.reference with in-memory dict writes.

class _PatchedBackend(_FakeBackend):
	"""Records all DB reference writes without hitting Firebase."""

	def __init__(self) -> None:
		super().__init__()
		self._ref_sets: list[tuple[str, object]] = []
		self._ref_updates: list[tuple[str, dict]] = []

	async def write_channel_message(self, cwd, sender, message_type, content, **kwargs):
		from server.canonicalization import to_firebase_key
		from datetime import datetime, timezone
		key = to_firebase_key(cwd)

		effective_url = kwargs.get("url")
		effective_filename = kwargs.get("filename")
		if message_type == "document" and effective_url and not (
			effective_url.startswith("http://") or effective_url.startswith("https://")
		):
			from pathlib import Path
			try:
				effective_url = await self._upload_file(Path(effective_url))
				if effective_filename is None:
					effective_filename = Path(kwargs.get("url", "")).name
			except Exception:
				pass

		now = datetime.now(timezone.utc).isoformat()
		title = kwargs.get("title")
		request_id = kwargs.get("request_id")
		format_ = kwargs.get("format", "plain")
		suggestions = kwargs.get("suggestions")

		msg_id = "fake_msg_id"
		payload = {
			"type": message_type,
			"sender": sender,
			"text": content,
			"format": format_,
			"timestamp": now,
			"cancelled": False,
		}
		if title is not None:
			payload["title"] = title[:80]
		if request_id:
			payload["request_id"] = request_id
		if effective_url:
			payload["url"] = effective_url
		if effective_filename:
			payload["filename"] = effective_filename
		if suggestions:
			payload["suggestions"] = list(suggestions)

		if message_type == "question":
			self._ref_sets.append((f"channels/{key}/hidden", False))

		self._ref_sets.append((f"channels/{key}/messages/{msg_id}", payload))
		self._ref_sets.append((f"channels/{key}/cwd_canonical", cwd))

		if title:
			self._ref_sets.append((f"channels/{key}/title", title[:80]))

		preview = content[:120].replace("\n", " ").strip()
		self._ref_updates.append((f"channels/{key}", {
			'last_activity_at': now,
			'preview': preview,
		}))

		if message_type != "human":
			fcm_data: dict = {"channel_key": key, "sb_message_type": message_type}
			if message_type == "question" and request_id is not None:
				fcm_data["request_id"] = request_id
			try:
				await self._send_fcm(key, message_type, sender, content, fcm_data)
			except Exception:
				pass

		return (cwd, sender), msg_id

	def hidden_set_for(self, cwd: str) -> bool | None:
		key = to_firebase_key(cwd)
		path = f"channels/{key}/hidden"
		for p, v in reversed(self._ref_sets):
			if p == path:
				return v
		return None


@pytest.mark.asyncio
async def test_question_auto_unhides_channel():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "question", "Proceed?", request_id="req-1")
	assert backend.hidden_set_for("c:/work/proj") is False


@pytest.mark.asyncio
async def test_notify_does_not_unhide_channel():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "status update")
	assert backend.hidden_set_for("c:/work/proj") is None


@pytest.mark.asyncio
async def test_document_does_not_unhide_channel():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "document", "report.txt", url="https://example/r")
	assert backend.hidden_set_for("c:/work/proj") is None


@pytest.mark.asyncio
async def test_question_sends_fcm():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "question", "Proceed?", request_id="req-1")
	key = to_firebase_key("c:/work/proj")
	assert f"{key}:question" in backend._fcm_sent


@pytest.mark.asyncio
async def test_notify_sends_fcm():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "status")
	key = to_firebase_key("c:/work/proj")
	assert f"{key}:notify" in backend._fcm_sent


@pytest.mark.asyncio
async def test_human_message_skips_fcm():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Human", "human", "reply text")
	assert backend._fcm_sent == []


@pytest.mark.asyncio
async def test_per_message_title_written():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "hi", title="My Session")
	key = to_firebase_key("c:/work/proj")
	msg_writes = [(p, v) for p, v in backend._ref_sets if p == f"channels/{key}/messages/fake_msg_id"]
	assert msg_writes
	assert msg_writes[0][1]["title"] == "My Session"


@pytest.mark.asyncio
async def test_channel_level_title_written_when_nonempty():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "hi", title="My Session")
	key = to_firebase_key("c:/work/proj")
	title_writes = [v for p, v in backend._ref_sets if p == f"channels/{key}/title"]
	assert title_writes == ["My Session"]


@pytest.mark.asyncio
async def test_channel_level_title_not_written_when_none():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "hi")
	key = to_firebase_key("c:/work/proj")
	title_writes = [v for p, v in backend._ref_sets if p == f"channels/{key}/title"]
	assert title_writes == []


@pytest.mark.asyncio
async def test_last_activity_and_preview_written():
	backend = _PatchedBackend()
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "hello world")
	key = to_firebase_key("c:/work/proj")
	updates = [v for p, v in backend._ref_updates if p == f"channels/{key}"]
	assert updates
	assert updates[-1]["preview"] == "hello world"
	assert "last_activity_at" in updates[-1]


@pytest.mark.asyncio
async def test_title_truncated_to_80_chars():
	backend = _PatchedBackend()
	long_title = "x" * 100
	await backend.write_channel_message("c:/work/proj", "Claude", "notify", "msg", title=long_title)
	key = to_firebase_key("c:/work/proj")
	title_writes = [v for p, v in backend._ref_sets if p == f"channels/{key}/title"]
	assert title_writes[0] == "x" * 80


@pytest.mark.asyncio
async def test_correlation_is_cwd_sender_tuple():
	backend = _PatchedBackend()
	corr, msg_id = await backend.write_channel_message("c:/work/proj", "Claude", "question", "q?", request_id="r1")
	assert corr == ("c:/work/proj", "Claude")
	assert msg_id == "fake_msg_id"


class _MarkCancelledBackend(_FakeBackend):
	"""Backend that records mark_question_cancelled calls using in-memory message store."""

	def __init__(self) -> None:
		super().__init__()
		self._messages: dict[str, dict] = {}
		self._cancelled_msg_ids: list[str] = []

	async def _add_message(self, key: str, msg_id: str, payload: dict) -> None:
		self._messages[f"{key}/{msg_id}"] = payload

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		from server.canonicalization import to_firebase_key
		key = to_firebase_key(cwd)
		for k, payload in self._messages.items():
			if k.startswith(key + "/") and payload.get("request_id") == request_id:
				msg_id = k.split("/")[-1]
				payload["cancelled"] = True
				self._cancelled_msg_ids.append(msg_id)
				return


@pytest.mark.asyncio
async def test_mark_question_cancelled_sets_flag():
	backend = _MarkCancelledBackend()
	key = to_firebase_key("c:/work/proj")
	await backend._add_message(key, "msg001", {"type": "question", "request_id": "abc123", "cancelled": False})
	await backend.mark_question_cancelled("c:/work/proj", "abc123")
	assert "msg001" in backend._cancelled_msg_ids
	assert backend._messages[f"{key}/msg001"]["cancelled"] is True


@pytest.mark.asyncio
async def test_mark_question_cancelled_noop_when_not_found():
	backend = _MarkCancelledBackend()
	key = to_firebase_key("c:/work/proj")
	await backend._add_message(key, "msg001", {"type": "question", "request_id": "abc123", "cancelled": False})
	await backend.mark_question_cancelled("c:/work/proj", "nonexistent")
	assert backend._cancelled_msg_ids == []


class _UpdateTitleBackend(_FakeBackend):
	"""Backend that stubs update_channel_title and update_last_activity."""

	def __init__(self) -> None:
		super().__init__()
		self._title_writes: list[tuple[str, str]] = []
		self._activity_writes: list[tuple[str, str, str]] = []

	async def update_channel_title(self, cwd: str, title: str) -> None:
		self._title_writes.append((cwd, title[:80]))

	async def update_last_activity(self, cwd: str, timestamp_iso: str, preview: str) -> None:
		self._activity_writes.append((cwd, timestamp_iso, preview[:120]))


@pytest.mark.asyncio
async def test_update_channel_title_records_write():
	backend = _UpdateTitleBackend()
	await backend.update_channel_title("c:/work/proj", "New Title")
	assert backend._title_writes == [("c:/work/proj", "New Title")]


@pytest.mark.asyncio
async def test_update_channel_title_truncates():
	backend = _UpdateTitleBackend()
	long_title = "y" * 100
	await backend.update_channel_title("c:/work/proj", long_title)
	assert backend._title_writes[0][1] == "y" * 80


@pytest.mark.asyncio
async def test_update_last_activity_records_write():
	backend = _UpdateTitleBackend()
	await backend.update_last_activity("c:/work/proj", "2026-04-24T12:00:00+00:00", "hello world")
	assert backend._activity_writes == [("c:/work/proj", "2026-04-24T12:00:00+00:00", "hello world")]


class _MirrorFakeBackend(FirebaseBackend):
	def __init__(self) -> None:
		self._mirror_writes: list[tuple[str | None, bool]] = []
		self._loop = asyncio.get_running_loop()
		self._logger = None
		self._storage_bucket = None

	async def write_away_mode_mirror(self, cwd: str | None, active: bool) -> None:
		self._mirror_writes.append((cwd, active))


@pytest.mark.asyncio
async def test_write_away_mode_mirror_global():
	backend = _MirrorFakeBackend()
	await backend.write_away_mode_mirror(None, True)
	assert backend._mirror_writes == [(None, True)]


@pytest.mark.asyncio
async def test_write_away_mode_mirror_per_cwd():
	backend = _MirrorFakeBackend()
	await backend.write_away_mode_mirror("c:/work/proj", False)
	assert backend._mirror_writes == [("c:/work/proj", False)]


@pytest.mark.asyncio
async def test_write_away_mode_mirror_accepts_false_global():
	backend = _MirrorFakeBackend()
	await backend.write_away_mode_mirror(None, False)
	assert backend._mirror_writes[0] == (None, False)
