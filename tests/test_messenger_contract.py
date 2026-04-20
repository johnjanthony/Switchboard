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
		"send_question",
		"send_notification",
		"send_timeout_followup",
		"send_resolution_confirmation",
		"poll_responses",
	}
	declared = {
		name
		for name, member in inspect.getmembers(MessengerBackend)
		if getattr(member, "__isabstractmethod__", False)
	}
	assert expected <= declared
