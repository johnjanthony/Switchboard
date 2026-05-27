"""Tests for server-side hydration from Firebase on startup."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from server.registry import Registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_firebase_db_mock(snapshot: dict):
	"""snapshot is a dict keyed by Firebase path; returns a mock that resolves
	each path to its snapshot value via .get()."""
	def reference(path: str):
		ref = MagicMock()
		ref.get.return_value = snapshot.get(path)
		return ref
	mock_db = MagicMock()
	mock_db.reference = reference
	return mock_db


def make_logger():
	logger = MagicMock()
	logger.surface_error = AsyncMock()
	logger.info = AsyncMock()
	return logger


def _run(coro):
	return asyncio.get_event_loop().run_until_complete(coro)


def member_data(**kwargs):
	base = {
		"cli_session_id": "sess-default",
		"sender": "Claude",
		"cwd": "c:/work/sw",
		"surface": "windows",
		"joined_at": 1000.0,
		"alive": True,
		"session_lost_permanently": False,
		"session_ended_at": None,
		"session_end_reason": None,
		"left_at": None,
		"last_seen_seq": 0,
	}
	base.update(kwargs)
	return base


def conv_snapshot(conv_id, state="active", title=None, members=None, messages=None, extra_meta=None):
	meta = {
		"state": state,
		"title": title or conv_id,
		"created_at": 1000.0,
		"last_activity_at": 1001.0,
		"ended_at": None,
		"hidden": False,
	}
	if extra_meta:
		meta.update(extra_meta)
	node = {"meta": meta}
	if members:
		node["members_active"] = members
	if messages:
		node["messages"] = messages
	return node


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hydrate_empty_firebase_leaves_registry_clean():
	"""Fresh install / empty Firebase: no errors, registry remains empty."""
	registry = Registry()
	logger = make_logger()
	mock_db = make_firebase_db_mock({})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry.conversations == {}
	assert registry._session_to_conversation_id == {}
	assert registry._session_home_conversation_id == {}
	assert registry._open_conversation_id is None
	assert registry._global_away is False
	logger.surface_error.assert_not_called()


@pytest.mark.asyncio
async def test_hydrate_restores_global_away_mode():
	"""Firebase has global_settings/away_mode=True → registry._global_away == True after hydration."""
	registry = Registry()
	logger = make_logger()
	mock_db = make_firebase_db_mock({
		"global_settings/away_mode": True,
	})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry._global_away is True


@pytest.mark.asyncio
async def test_hydrate_restores_open_conversation_id():
	"""Firebase has global_settings/open_conversation_id pointing at an Active conv
	→ registry._open_conversation_id set after hydration."""
	registry = Registry()
	logger = make_logger()
	mock_db = make_firebase_db_mock({
		"global_settings/open_conversation_id": "conv-open-42",
		"conversations": {
			"conv-open-42": conv_snapshot("conv-open-42", state="active"),
		},
	})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry._open_conversation_id == "conv-open-42"


@pytest.mark.asyncio
async def test_hydrate_clears_dangling_open_pointer_to_ended_conv():
	"""Firebase open_conversation_id points at a conv whose state is Ended (so it
	doesn't get hydrated). Hydration must clear the dangling pointer — in memory
	AND on the backend — instead of leaving a stale id that every enter_conversation
	call will reject. Reproduces the divergence observed 2026-05-27 after the
	set_open_conversation_id(None) bug caused a clear to silently fail."""
	registry = Registry()
	logger = make_logger()
	backend = MagicMock()
	backend.set_open_conversation_id = AsyncMock()
	mock_db = make_firebase_db_mock({
		"global_settings/open_conversation_id": "conv-stale",
		"conversations": {
			"conv-stale": conv_snapshot("conv-stale", state="ended"),
		},
	})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, backend, logger)

	assert registry._open_conversation_id is None
	backend.set_open_conversation_id.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_hydrate_clears_dangling_open_pointer_to_missing_conv():
	"""Firebase open_conversation_id points at a conv that doesn't exist in
	Firebase at all (deleted, orphaned). Same outcome as the Ended case: clear."""
	registry = Registry()
	logger = make_logger()
	backend = MagicMock()
	backend.set_open_conversation_id = AsyncMock()
	mock_db = make_firebase_db_mock({
		"global_settings/open_conversation_id": "conv-ghost",
		# no /conversations node at all
	})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, backend, logger)

	assert registry._open_conversation_id is None
	backend.set_open_conversation_id.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_hydrate_does_not_touch_backend_when_open_pointer_is_valid():
	"""Pointer references a live Active conv: leave it alone, no backend write."""
	registry = Registry()
	logger = make_logger()
	backend = MagicMock()
	backend.set_open_conversation_id = AsyncMock()
	mock_db = make_firebase_db_mock({
		"global_settings/open_conversation_id": "conv-live",
		"conversations": {
			"conv-live": conv_snapshot("conv-live", state="active"),
		},
	})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, backend, logger)

	assert registry._open_conversation_id == "conv-live"
	backend.set_open_conversation_id.assert_not_called()


@pytest.mark.asyncio
async def test_hydrate_single_active_conversation():
	"""One Active conversation with one alive member: full state restored
	including messages, session binding, and home pointer."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-1": conv_snapshot(
				"conv-1",
				title="My Session",
				members={"Claude": member_data(cli_session_id="sess-abc", sender="Claude", alive=True)},
				messages={
					"-push1": {"seq": 0, "sender": "Claude", "type": "notify", "text": "hello", "timestamp": "t1"},
					"-push2": {"seq": 1, "sender": "John", "type": "human", "text": "hi", "timestamp": "t2"},
				},
			)
		},
		"cli_sessions": {
			"sess-abc": {"home_conversation_id": "conv-1"},
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert "conv-1" in registry.conversations
	conv = registry.conversations["conv-1"]
	assert conv.title == "My Session"
	assert conv.state == "active"
	assert "Claude" in conv.members_active
	assert conv.members_active["Claude"].cli_session_id == "sess-abc"
	assert conv.members_active["Claude"].alive is True
	assert len(conv.messages) == 2
	# Session binding derived from alive member
	assert registry._session_to_conversation_id.get("sess-abc") == "conv-1"
	# Home pointer from cli_sessions
	assert registry._session_home_conversation_id.get("sess-abc") == "conv-1"


@pytest.mark.asyncio
async def test_hydrate_skips_ended_conversations():
	"""Conversation with state='ended' is NOT loaded into registry.conversations."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-active": conv_snapshot("conv-active", state="active"),
			"conv-ended": conv_snapshot("conv-ended", state="ended"),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert "conv-active" in registry.conversations
	assert "conv-ended" not in registry.conversations


@pytest.mark.asyncio
async def test_hydrate_preserves_dormant_members():
	"""A member with alive=False, session_lost_permanently=False is loaded as-is.
	Dormancy persists across restarts; combine/resume can revive them."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-dormant": conv_snapshot(
				"conv-dormant",
				members={
					"Claude": member_data(
						cli_session_id="sess-dormant",
						sender="Claude",
						alive=False,
						session_lost_permanently=False,
					),
				},
			),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	conv = registry.conversations["conv-dormant"]
	assert "Claude" in conv.members_active
	member = conv.members_active["Claude"]
	assert member.alive is False
	assert member.session_lost_permanently is False


@pytest.mark.asyncio
async def test_hydrate_derives_session_to_conversation_id_for_alive_and_resumable_dormant():
	"""session_to_conversation_id contains bindings for alive members AND dormant
	members that are not permanently lost (resumable). Permanently-lost dormant
	members are excluded — their session_ids are stale.

	This enables `claude --resume <session_id>` post-restart to land in the right
	conversation for all resumable agents."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-mixed": conv_snapshot(
				"conv-mixed",
				members={
					"AliveAgent": member_data(cli_session_id="sess-alive", sender="AliveAgent", alive=True),
					"DormantAgent": member_data(cli_session_id="sess-dormant", sender="DormantAgent", alive=False, session_lost_permanently=False),
					"LostAgent": member_data(cli_session_id="sess-lost", sender="LostAgent", alive=False, session_lost_permanently=True),
				},
			),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	# Alive member gets binding
	assert registry._session_to_conversation_id.get("sess-alive") == "conv-mixed"
	# Resumable dormant member gets binding (supports `claude --resume` post-restart)
	assert registry._session_to_conversation_id.get("sess-dormant") == "conv-mixed"
	# Permanently-lost member does NOT get binding (session_id is stale)
	assert "sess-lost" not in registry._session_to_conversation_id


@pytest.mark.asyncio
async def test_hydrate_orders_messages_by_push_key():
	"""Messages stored under Firebase push keys are ordered chronologically
	(push keys sort lexicographically by time)."""
	registry = Registry()
	logger = make_logger()

	# Deliberately provide push keys out of alphabetical/time order
	snapshot = {
		"conversations": {
			"conv-msgs": conv_snapshot(
				"conv-msgs",
				messages={
					"-zzz": {"seq": 2, "sender": "John", "type": "human", "text": "third"},
					"-aaa": {"seq": 0, "sender": "Claude", "type": "notify", "text": "first"},
					"-mmm": {"seq": 1, "sender": "Claude", "type": "notify", "text": "second"},
				},
			),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	conv = registry.conversations["conv-msgs"]
	assert len(conv.messages) == 3
	texts = [m["text"] for m in conv.messages]
	assert texts == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_hydrate_continues_after_malformed_conversation():
	"""One conversation has corrupt data; others should still be hydrated."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-corrupt": "this is not a dict",
			"conv-good": conv_snapshot("conv-good", title="Good One"),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	# Good conversation was hydrated
	assert "conv-good" in registry.conversations
	assert registry.conversations["conv-good"].title == "Good One"
	# Corrupt one was skipped (no crash)
	assert "conv-corrupt" not in registry.conversations


@pytest.mark.asyncio
async def test_hydrate_handles_missing_meta():
	"""Conversation with missing/empty meta is skipped (degenerate state)."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-no-meta": {"members_active": {}},  # no meta key
			"conv-empty-meta": {"meta": None},
			"conv-ok": conv_snapshot("conv-ok"),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert "conv-no-meta" not in registry.conversations
	assert "conv-empty-meta" not in registry.conversations
	assert "conv-ok" in registry.conversations


@pytest.mark.asyncio
async def test_hydrate_handles_partial_member_data():
	"""Member missing cli_session_id is skipped; others in same conv are loaded."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-partial": conv_snapshot(
				"conv-partial",
				members={
					"NoSession": {"sender": "NoSession", "cwd": "", "surface": "windows", "joined_at": 0.0, "alive": True},
					"HasSession": member_data(cli_session_id="sess-valid", sender="HasSession"),
				},
			),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	conv = registry.conversations["conv-partial"]
	assert "HasSession" in conv.members_active
	assert "NoSession" not in conv.members_active
	assert conv.members_active["HasSession"].cli_session_id == "sess-valid"


@pytest.mark.asyncio
async def test_hydrate_restores_members_history():
	"""Departed members under /conversations/<id>/members_history are restored
	with parting metadata (left_at, session_ended_at, session_end_reason)."""
	registry = Registry()
	logger = make_logger()

	departed_data = member_data(
		cli_session_id="sess-departed",
		sender="Departed",
		alive=False,
		left_at=2000.0,
		session_ended_at="2026-05-26T12:00:00Z",
		session_end_reason="hook_sessionend",
		last_seen_seq=7,
	)
	snapshot = {
		"conversations": {
			"conv-with-history": {
				"meta": {
					"state": "active",
					"title": "Has History",
					"created_at": 1000.0,
					"last_activity_at": 2000.0,
					"ended_at": None,
					"hidden": False,
				},
				"members_active": {
					"Alive": member_data(cli_session_id="sess-alive", sender="Alive", alive=True),
				},
				"members_history": {
					"Departed": departed_data,
				},
			}
		}
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	conv = registry.conversations["conv-with-history"]
	assert len(conv.members_history) == 1
	departed = conv.members_history[0]
	assert departed.sender == "Departed"
	assert departed.cli_session_id == "sess-departed"
	assert departed.alive is False
	assert departed.left_at == 2000.0
	assert departed.session_ended_at == "2026-05-26T12:00:00Z"
	assert departed.session_end_reason == "hook_sessionend"
	assert departed.last_seen_seq == 7
	# Active members still loaded
	assert "Alive" in conv.members_active
	assert conv.members_active["Alive"].cli_session_id == "sess-alive"
