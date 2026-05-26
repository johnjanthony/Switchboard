"""Tests for the session-fallback rule."""

import pytest

from server.session_fallback import compute_fallback


def test_unbind_when_away_off():
	decision = compute_fallback(
		session_id="s-1",
		home_conversation_id="conv-home",
		home_state="active",
		global_away_mode=False,
	)
	assert decision == ("unbind", None)


def test_rebind_home_when_away_on_and_home_active():
	decision = compute_fallback(
		session_id="s-1",
		home_conversation_id="conv-home",
		home_state="active",
		global_away_mode=True,
	)
	assert decision == ("rebind_home", "conv-home")


def test_create_new_when_away_on_and_home_ended():
	decision = compute_fallback(
		session_id="s-1",
		home_conversation_id="conv-home",
		home_state="ended",
		global_away_mode=True,
	)
	assert decision == ("create_new", None)


def test_create_new_when_away_on_and_no_home():
	decision = compute_fallback(
		session_id="s-1",
		home_conversation_id=None,
		home_state=None,
		global_away_mode=True,
	)
	assert decision == ("create_new", None)


@pytest.fixture
def mock_registry():
	"""Minimal mock — just the surfaces apply_fallback touches."""
	from server.registry import Registry
	r = Registry()
	return r


def test_apply_fallback_unbind(mock_registry):
	"""Away off → session is unbound."""
	from server.session_fallback import apply_fallback
	mock_registry._global_away = False
	mock_registry.bind_session("s-1", "conv-x")
	apply_fallback(mock_registry, session_id="s-1")
	assert "s-1" not in mock_registry.session_to_conversation_id


def test_apply_fallback_rebind_home(mock_registry):
	"""Away on + home Active → re-bind to home."""
	from server.session_fallback import apply_fallback
	from server.registry import Conversation
	mock_registry._global_away = True
	mock_registry.set_session_home("s-1", "conv-home")
	mock_registry.conversations["conv-home"] = Conversation(id="conv-home", title="home")
	apply_fallback(mock_registry, session_id="s-1")
	assert mock_registry.session_to_conversation_id["s-1"] == "conv-home"


def test_apply_fallback_create_new(mock_registry):
	"""Away on + home Ended → mint new Active conversation, update home pointer."""
	from server.session_fallback import apply_fallback
	from server.registry import Conversation
	mock_registry._global_away = True
	mock_registry.set_session_home("s-1", "conv-old")
	mock_registry.conversations["conv-old"] = Conversation(id="conv-old", title="old", state="ended")
	apply_fallback(mock_registry, session_id="s-1")
	new_conv_id = mock_registry.session_to_conversation_id["s-1"]
	assert new_conv_id != "conv-old"
	assert mock_registry.conversations[new_conv_id].state == "active"
	assert mock_registry.session_home_conversation_id["s-1"] == new_conv_id
