"""Contract tests for the MessengerBackend interface."""

import inspect

import pytest

from server.messenger import IncomingResponse, MessengerBackend


def test_incoming_response_is_simple_dataclass():
	r = IncomingResponse(correlation=42, text="yes")
	assert r.correlation == 42
	assert r.text == "yes"


def test_messenger_backend_is_abstract():
	with pytest.raises(TypeError):
		MessengerBackend()  # type: ignore[abstract]


def test_messenger_backend_declares_required_methods():
	expected = {
		"write_channel_message",
		"send_timeout_followup",
		"send_resolution_confirmation",
		"poll_responses",
		"poll_commands",
		"aclose",
	}
	declared = {
		name
		for name, member in inspect.getmembers(MessengerBackend)
		if getattr(member, "__isabstractmethod__", False)
	}
	assert expected <= declared


def test_messenger_backend_new_methods_exist():
	"""New no-op default methods must be declared on MessengerBackend."""
	for method_name in (
		"mark_question_cancelled",
		"send_stale_reply_notice",
		"update_channel_title",
		"update_last_activity",
		"write_away_mode_mirror",
		"poll_spawn_collision_decision",
		"load_away_mode_snapshot",
		"start_away_mode_listeners",
		"reset_all_pending_responses",
		"delete_legacy_away_mode_node",
	):
		assert hasattr(MessengerBackend, method_name), f"Missing: {method_name}"


def test_poll_spawn_collision_decision_signature():
	"""poll_spawn_collision_decision must accept a spawn_id and return a decision dict."""
	import asyncio
	sig = inspect.signature(MessengerBackend.poll_spawn_collision_decision)
	params = list(sig.parameters)
	assert params == ["self", "spawn_id"]
	# Default is NotImplementedError so callers know to opt in via FirebaseBackend.
	with pytest.raises(NotImplementedError):
		asyncio.run(_call_default_poll_spawn_collision_decision())


async def _call_default_poll_spawn_collision_decision():
	# Helper: instantiate a minimal subclass that doesn't override the method.
	class _Stub(MessengerBackend):
		async def write_channel_message(self, *a, **kw): return None, None
		async def send_timeout_followup(self, *a, **kw): pass
		async def send_resolution_confirmation(self, *a, **kw): pass
		async def send_text(self, *a, **kw): pass
		async def poll_responses(self):
			if False:
				yield
		async def poll_commands(self):
			if False:
				yield
		async def send_spawn_ack(self, *a, **kw): pass
		async def write_session_meta(self, *a, **kw): pass
		async def aclose(self): pass

	stub = _Stub()
	await stub.poll_spawn_collision_decision("any-spawn-id")


def test_write_away_mode_mirror_signature():
	"""write_away_mode_mirror must accept (cwd: str | None, active: bool)."""
	sig = inspect.signature(MessengerBackend.write_away_mode_mirror)
	params = list(sig.parameters)
	assert params == ["self", "cwd", "active"]


def test_mark_question_cancelled_signature():
	sig = inspect.signature(MessengerBackend.mark_question_cancelled)
	params = list(sig.parameters)
	assert params == ["self", "cwd", "request_id"]


def test_send_stale_reply_notice_signature():
	sig = inspect.signature(MessengerBackend.send_stale_reply_notice)
	params = list(sig.parameters)
	assert params == ["self", "cwd", "sender"]


def test_write_channel_message_accepts_rejected_kwarg():
	"""FirebaseBackend.send_stale_reply_notice writes via write_channel_message
	with rejected=True so the Android client can render the system message as a
	transient toast. Lock the kwarg in on the ABC so future overrides can't
	silently drop it (which would TypeError at runtime, untested today)."""
	sig = inspect.signature(MessengerBackend.write_channel_message)
	assert "rejected" in sig.parameters


def test_update_channel_title_signature():
	sig = inspect.signature(MessengerBackend.update_channel_title)
	params = list(sig.parameters)
	assert params == ["self", "cwd", "title"]


def test_update_last_activity_signature():
	sig = inspect.signature(MessengerBackend.update_last_activity)
	params = list(sig.parameters)
	assert params == ["self", "cwd", "timestamp_iso", "preview"]


class _StubBackend(MessengerBackend):
	"""Minimal concrete subclass implementing only the abstract methods."""

	async def write_channel_message(self, *a, **k):
		return None, None

	async def send_timeout_followup(self, *a, **k):
		return None

	async def send_resolution_confirmation(self, *a, **k):
		return None

	async def poll_responses(self):
		if False:
			yield None

	async def poll_commands(self):
		if False:
			yield None

	async def aclose(self):
		pass


@pytest.mark.asyncio
async def test_write_away_mode_mirror_default_is_noop():
	backend = _StubBackend()
	await backend.write_away_mode_mirror(None, True)
	await backend.write_away_mode_mirror("c:/work/proj", False)
	await backend.write_away_mode_mirror(None, False)


@pytest.mark.asyncio
async def test_mark_question_cancelled_default_is_noop():
	backend = _StubBackend()
	await backend.mark_question_cancelled("c:/work/sw", "abc12345")
	await backend.mark_question_cancelled("c:/work/other", "00000000")


@pytest.mark.asyncio
async def test_send_stale_reply_notice_default_is_noop():
	backend = _StubBackend()
	await backend.send_stale_reply_notice("c:/work/sw", "Claude")


@pytest.mark.asyncio
async def test_update_channel_title_default_is_noop():
	backend = _StubBackend()
	await backend.update_channel_title("c:/work/sw", "My Title")


@pytest.mark.asyncio
async def test_update_last_activity_default_is_noop():
	backend = _StubBackend()
	await backend.update_last_activity("c:/work/sw", "2026-04-24T00:00:00+00:00", "hello")


class _RecordingBackend(MessengerBackend):
	def __init__(self) -> None:
		self.mirror_calls: list[tuple] = []
		self.cancel_calls: list[tuple] = []
		self.stale_calls: list[tuple] = []
		self.title_calls: list[tuple] = []
		self.activity_calls: list[tuple] = []

	async def write_channel_message(self, *a, **k):
		return None, None

	async def send_timeout_followup(self, *a, **k):
		return None

	async def send_resolution_confirmation(self, *a, **k):
		return None

	async def poll_responses(self):
		if False:
			yield None

	async def poll_commands(self):
		if False:
			yield None

	async def aclose(self) -> None:
		pass

	async def write_away_mode_mirror(self, cwd: str | None, active: bool) -> None:
		self.mirror_calls.append((cwd, active))

	async def mark_question_cancelled(self, cwd: str, request_id: str) -> None:
		self.cancel_calls.append((cwd, request_id))

	async def send_stale_reply_notice(self, cwd: str, sender: str) -> None:
		self.stale_calls.append((cwd, sender))

	async def update_channel_title(self, cwd: str, title: str) -> None:
		self.title_calls.append((cwd, title))

	async def update_last_activity(self, cwd: str, timestamp_iso: str, preview: str) -> None:
		self.activity_calls.append((cwd, timestamp_iso, preview))

