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
	"""Away on + home Active (alive session) → re-bind to home."""
	from server.session_fallback import apply_fallback
	from server.registry import Conversation
	mock_registry._global_away = True
	mock_registry.set_session_home("s-1", "conv-home")
	mock_registry.conversations["conv-home"] = Conversation(id="conv-home", title="home")
	# Session must be bound so apply_fallback takes the alive path; an unbound
	# session against an Active home takes the dormant short-circuit and
	# leaves the home pointer alone (no rebind).
	mock_registry.bind_session("s-1", "conv-other")
	apply_fallback(mock_registry, session_id="s-1")
	assert mock_registry.session_to_conversation_id["s-1"] == "conv-home"


def test_apply_fallback_create_new(mock_registry):
	"""Away on + home Ended (alive session) → mint new Active conversation, update home pointer."""
	from server.session_fallback import apply_fallback
	from server.registry import Conversation
	mock_registry._global_away = True
	mock_registry.set_session_home("s-1", "conv-old")
	mock_registry.conversations["conv-old"] = Conversation(id="conv-old", title="old", state="ended")
	# Session must be bound for the create_new path; an unbound session takes
	# the dormant short-circuit (Fix Pack 4) which never mints a new conv.
	mock_registry.bind_session("s-1", "conv-old")
	apply_fallback(mock_registry, session_id="s-1")
	new_conv_id = mock_registry.session_to_conversation_id["s-1"]
	assert new_conv_id != "conv-old"
	assert mock_registry.conversations[new_conv_id].state == "active"
	assert mock_registry.session_home_conversation_id["s-1"] == new_conv_id


def test_apply_fallback_dormant_with_ended_home_clears_pointer(mock_registry):
	"""Dormant session (unbound) with home pointer at an Ended conv:
	home pointer is cleared; no unbind, no new conv created."""
	from server.session_fallback import apply_fallback
	from server.registry import Conversation
	mock_registry._global_away = True
	mock_registry.set_session_home("s-dorm", "conv-old")
	mock_registry.conversations["conv-old"] = Conversation(id="conv-old", title="old", state="ended")
	# Session is NOT in session_to_conversation_id → dormant path.
	conv_count_before = len(mock_registry.conversations)
	apply_fallback(mock_registry, session_id="s-dorm")
	# Home pointer cleared.
	assert "s-dorm" not in mock_registry.session_home_conversation_id
	# Still unbound (no new binding created).
	assert "s-dorm" not in mock_registry.session_to_conversation_id
	# No new conversation was minted.
	assert len(mock_registry.conversations) == conv_count_before


def test_apply_fallback_dormant_with_missing_home_clears_pointer(mock_registry):
	"""Dormant session whose home points at a conv that no longer exists in
	registry.conversations: still cleared (defensive — covers stale pointers)."""
	from server.session_fallback import apply_fallback
	mock_registry._global_away = True
	mock_registry.set_session_home("s-dorm", "conv-vanished")
	# No Conversation entry for conv-vanished.
	apply_fallback(mock_registry, session_id="s-dorm")
	assert "s-dorm" not in mock_registry.session_home_conversation_id


def test_apply_fallback_dormant_with_active_home_leaves_pointer(mock_registry):
	"""Edge case: dormant session whose home is still ACTIVE. Don't clear —
	the pointer is a valid resume target (claude --resume could re-bind)."""
	from server.session_fallback import apply_fallback
	from server.registry import Conversation
	mock_registry._global_away = True
	mock_registry.set_session_home("s-dorm", "conv-still-live")
	mock_registry.conversations["conv-still-live"] = Conversation(
		id="conv-still-live", title="live", state="active",
	)
	apply_fallback(mock_registry, session_id="s-dorm")
	# Home pointer preserved.
	assert mock_registry.session_home_conversation_id["s-dorm"] == "conv-still-live"
	# Still unbound (dormant path never binds).
	assert "s-dorm" not in mock_registry.session_to_conversation_id


def test_apply_fallback_dormant_with_no_home_is_noop(mock_registry):
	"""Dormant session with no home pointer at all: do nothing."""
	from server.session_fallback import apply_fallback
	# Neither bound nor has a home.
	apply_fallback(mock_registry, session_id="s-dorm")
	assert "s-dorm" not in mock_registry.session_home_conversation_id
	assert "s-dorm" not in mock_registry.session_to_conversation_id
