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
	# No-op default must not raise, must accept both bool values.
	await backend.write_away_mode_mirror(True)
	await backend.write_away_mode_mirror(False)


class _RecordingBackend(MessengerBackend):
	def __init__(self) -> None:
		self.mirror_calls: list[bool] = []

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

	async def write_away_mode_mirror(self, active: bool) -> None:
		self.mirror_calls.append(active)


@pytest.mark.asyncio
async def test_multibackend_forwards_write_away_mode_mirror():
	from server.messenger import MultiBackend
	b1 = _RecordingBackend()
	b2 = _RecordingBackend()
	multi = MultiBackend([b1, b2])
	await multi.write_away_mode_mirror(True)
	await multi.write_away_mode_mirror(False)
	assert b1.mirror_calls == [True, False]
	assert b2.mirror_calls == [True, False]
