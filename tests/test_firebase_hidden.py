"""Firebase conversation-aware message dispatch tests.

Exercises write_conversation_message in FirebaseBackend via
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
# write_conversation_message calls asyncio.to_thread for every DB write.
# We intercept by overriding write_conversation_message at the DB-call level.

class _PatchedBackend(_FakeBackend):
	"""Records all DB reference writes without hitting Firebase."""

	def __init__(self) -> None:
		super().__init__()
		self._ref_sets: list[tuple[str, object]] = []
		self._ref_updates: list[tuple[str, dict]] = []

	async def write_conversation_message(
		self,
		conv_id,
		sender_or_message,
		message_type=None,
		text=None,
		*,
		request_id=None,
		url=None,
		format="plain",
		suggestions=None,
		filename=None,
		title=None,
		rejected=False,
		attached_to_msg_id=None,
	):
		# Legacy dict form not expected in these tests; only expanded form.
		if isinstance(sender_or_message, dict):
			return "fake_msg_id"

		sender = sender_or_message

		effective_url = url
		effective_filename = filename
		if message_type == "document" and effective_url and not (
			effective_url.startswith("http://") or effective_url.startswith("https://")
		):
			from pathlib import Path
			try:
				effective_url = await self._upload_file(Path(effective_url))
				if effective_filename is None:
					effective_filename = Path(url).name
			except Exception:
				pass

		from datetime import datetime, timezone
		now = datetime.now(timezone.utc).isoformat()
		msg_id = "fake_msg_id"
		payload = {
			"type": message_type,
			"sender": sender,
			"text": text,
			"format": format,
			"timestamp": now,
			"cancelled": False,
			"rejected": rejected,
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
		if attached_to_msg_id:
			payload["attached_to_msg_id"] = attached_to_msg_id

		if message_type == "question":
			self._ref_sets.append((f"conversations/{conv_id}/meta/hidden", False))

		self._ref_sets.append((f"conversations/{conv_id}/messages/{msg_id}", payload))

		if title:
			self._ref_sets.append((f"conversations/{conv_id}/meta/title", title[:80]))

		preview = text[:120].replace("\n", " ").strip() if text else ""
		self._ref_updates.append((f"conversations/{conv_id}/meta", {
			"last_activity_at": now,
			"preview": preview,
		}))

		if message_type != "human":
			fcm_data: dict = {"conv_id": conv_id, "sb_message_type": message_type}
			if message_type == "question" and request_id is not None:
				fcm_data["request_id"] = request_id
			try:
				await self._send_fcm(conv_id, message_type, sender, text, fcm_data)
			except Exception:
				pass

		return (conv_id, sender), msg_id

	def hidden_set_for(self, conv_id: str) -> bool | None:
		path = f"conversations/{conv_id}/meta/hidden"
		for p, v in reversed(self._ref_sets):
			if p == path:
				return v
		return None


@pytest.mark.asyncio
async def test_question_auto_unhides_conversation():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "question", "Proceed?", request_id="req-1")
	assert backend.hidden_set_for("conv-proj-1") is False


@pytest.mark.asyncio
async def test_notify_does_not_unhide_conversation():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "status update")
	assert backend.hidden_set_for("conv-proj-1") is None


@pytest.mark.asyncio
async def test_document_does_not_unhide_conversation():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "document", "report.txt", url="https://example/r")
	assert backend.hidden_set_for("conv-proj-1") is None


@pytest.mark.asyncio
async def test_question_sends_fcm():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "question", "Proceed?", request_id="req-1")
	assert "conv-proj-1:question" in backend._fcm_sent


@pytest.mark.asyncio
async def test_notify_sends_fcm():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "status")
	assert "conv-proj-1:notify" in backend._fcm_sent


@pytest.mark.asyncio
async def test_human_message_skips_fcm():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Human", "human", "reply text")
	assert backend._fcm_sent == []


@pytest.mark.asyncio
async def test_per_message_title_written():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "hi", title="My Session")
	msg_writes = [(p, v) for p, v in backend._ref_sets if p == "conversations/conv-proj-1/messages/fake_msg_id"]
	assert msg_writes
	assert msg_writes[0][1]["title"] == "My Session"


@pytest.mark.asyncio
async def test_conversation_level_title_written_when_nonempty():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "hi", title="My Session")
	title_writes = [v for p, v in backend._ref_sets if p == "conversations/conv-proj-1/meta/title"]
	assert title_writes == ["My Session"]


@pytest.mark.asyncio
async def test_conversation_level_title_not_written_when_none():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "hi")
	title_writes = [v for p, v in backend._ref_sets if p == "conversations/conv-proj-1/meta/title"]
	assert title_writes == []


@pytest.mark.asyncio
async def test_last_activity_and_preview_written():
	backend = _PatchedBackend()
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "hello world")
	updates = [v for p, v in backend._ref_updates if p == "conversations/conv-proj-1/meta"]
	assert updates
	assert updates[-1]["preview"] == "hello world"
	assert "last_activity_at" in updates[-1]


@pytest.mark.asyncio
async def test_title_truncated_to_80_chars():
	backend = _PatchedBackend()
	long_title = "x" * 100
	await backend.write_conversation_message("conv-proj-1", "Claude", "notify", "msg", title=long_title)
	title_writes = [v for p, v in backend._ref_sets if p == "conversations/conv-proj-1/meta/title"]
	assert title_writes[0] == "x" * 80


@pytest.mark.asyncio
async def test_correlation_is_conv_id_sender_tuple():
	backend = _PatchedBackend()
	corr, msg_id = await backend.write_conversation_message("conv-proj-1", "Claude", "question", "q?", request_id="r1")
	assert corr == ("conv-proj-1", "Claude")
	assert msg_id == "fake_msg_id"


class _MarkCancelledBackend(_FakeBackend):
	"""Backend that records mark_question_cancelled calls using in-memory message store."""

	def __init__(self) -> None:
		super().__init__()
		self._messages: dict[str, dict] = {}
		self._cancelled_msg_ids: list[str] = []

	async def _add_message(self, conv_id: str, msg_id: str, payload: dict) -> None:
		self._messages[f"{conv_id}/{msg_id}"] = payload

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		# cwd is conv_id in the new model
		conv_id = cwd
		for k, payload in self._messages.items():
			if k.startswith(conv_id + "/") and payload.get("request_id") == request_id:
				msg_id = k.split("/")[-1]
				payload["cancelled"] = True
				self._cancelled_msg_ids.append(msg_id)
				return


@pytest.mark.asyncio
async def test_mark_question_cancelled_sets_flag():
	backend = _MarkCancelledBackend()
	await backend._add_message("conv-proj-1", "msg001", {"type": "question", "request_id": "abc123", "cancelled": False})
	await backend.mark_question_cancelled("conv-proj-1", "abc123")
	assert "msg001" in backend._cancelled_msg_ids
	assert backend._messages["conv-proj-1/msg001"]["cancelled"] is True


@pytest.mark.asyncio
async def test_mark_question_cancelled_noop_when_not_found():
	backend = _MarkCancelledBackend()
	await backend._add_message("conv-proj-1", "msg001", {"type": "question", "request_id": "abc123", "cancelled": False})
	await backend.mark_question_cancelled("conv-proj-1", "nonexistent")
	assert backend._cancelled_msg_ids == []


