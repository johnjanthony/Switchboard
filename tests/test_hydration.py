"""Tests for server-side hydration from Firebase on startup."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from server.registry import Registry
from server.session_registry import SessionRegistry


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
	assert "sess-abc" in conv.members_active
	assert conv.members_active["sess-abc"].cli_session_id == "sess-abc"
	assert conv.members_active["sess-abc"].alive is True
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
	assert "sess-dormant" in conv.members_active
	member = conv.members_active["sess-dormant"]
	assert member.alive is False
	assert member.session_lost_permanently is False


@pytest.mark.asyncio
async def test_hydrate_binds_alive_members_only():
	"""session_to_conversation_id contains bindings for alive members ONLY.

	Dormant members (resumable or lost) stay unbound: the steady-state
	invariant is "dormant = unbound" (cli_session_end clears the binding when
	a CLI dies), and resume eligibility + apply_fallback's dormant
	short-circuit both rely on it. Resume re-establishes the binding when it
	actually relaunches the member (P0-2, decided 2026-06-11)."""
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
	# Dormant member does NOT get binding (dormant = unbound; resume rebinds)
	assert "sess-dormant" not in registry._session_to_conversation_id
	# Permanently-lost member does NOT get binding
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
	assert "sess-valid" in conv.members_active
	assert len(conv.members_active) == 1
	assert conv.members_active["sess-valid"].cli_session_id == "sess-valid"


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
	assert "sess-alive" in conv.members_active
	assert conv.members_active["sess-alive"].cli_session_id == "sess-alive"


@pytest.mark.asyncio
async def test_force_end_after_hydration_does_not_mint_orphan_home_conv():
	"""Rejected-option regression (spec §3 P0-2): if hydration re-bound dormant
	members, apply_fallback would treat them as live on force-end and, with
	away mode on, its create_new arm would mint an orphan '(home)'
	conversation for a dead session."""
	registry = Registry()
	logger = make_logger()
	snapshot = {
		"global_settings/away_mode": True,
		"conversations": {
			"conv-d": conv_snapshot(
				"conv-d",
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
	with patch("server.hydration.db", make_firebase_db_mock(snapshot)):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry._global_away is True  # away on: the create_new arm is reachable
	conv_count_before = len(registry.conversations)

	from server.gateway.dispatch import handle_force_end
	await handle_force_end(registry, "conv-d")

	assert registry.conversations["conv-d"].state == "ended"
	assert len(registry.conversations) == conv_count_before, \
		"force-end must not mint a '(home)' conversation for a dead session"


@pytest.mark.asyncio
async def test_hydrate_sessions_loads_terminal_and_nonterminal_records_without_remirroring():
	"""sessions/ children hydrate into the SessionRegistry regardless of state -
	terminal (ended) records included, since the sweeper needs them in memory to
	retention-prune their RTDB entries. hydrate_record must seed the mirror canon
	so hydration itself never re-fires a write back to Firebase."""
	registry = Registry()
	logger = make_logger()
	session_registry = SessionRegistry()

	mirror_calls = []
	session_registry.set_mirror(lambda sid, payload: mirror_calls.append((sid, payload)))

	snapshot = {
		"sessions": {
			"sess-idle": {
				"cli_session_id": "sess-idle",
				"cwd": "c:/work/sw",
				"surface": "windows",
				"cli": "claude",
				"started_at": "t0",
				"last_event_at": "t1",
				"state": "idle",
				"state_detail": None,
				"conversation_id": None,
				"sender": "Claude",
				"model": None,
				"context_pct": None,
				"end_reason": None,
			},
			"sess-ended": {
				"cli_session_id": "sess-ended",
				"cwd": "c:/work/sw",
				"surface": "windows",
				"cli": "claude",
				"started_at": "t0",
				"last_event_at": "t2",
				"state": "ended",
				"state_detail": None,
				"conversation_id": None,
				"sender": "Claude",
				"model": None,
				"context_pct": None,
				"end_reason": "hook_sessionend",
			},
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger, session_registry=session_registry)

	idle_rec = session_registry.get("sess-idle")
	assert idle_rec is not None
	assert idle_rec.state == "idle"
	assert idle_rec.last_event_at == "t1"
	assert idle_rec.source == "hydration"

	ended_rec = session_registry.get("sess-ended")
	assert ended_rec is not None
	assert ended_rec.state == "ended"
	assert ended_rec.last_event_at == "t2"
	assert ended_rec.source == "hydration"

	assert mirror_calls == [], "hydrate_record must seed the mirror canon, not re-fire writes"


@pytest.mark.asyncio
async def test_hydrate_restores_origin():
	"""A meta payload carrying origin=join round-trips onto the hydrated Conversation.origin."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-origin": conv_snapshot("conv-origin", extra_meta={"origin": "join"}),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry.conversations["conv-origin"].origin == "join"


@pytest.mark.asyncio
async def test_hydrate_missing_origin_is_none():
	"""A meta payload without the origin key hydrates origin is None (pre-origin record)."""
	registry = Registry()
	logger = make_logger()

	snapshot = {
		"conversations": {
			"conv-no-origin": conv_snapshot("conv-no-origin"),
		},
	}
	mock_db = make_firebase_db_mock(snapshot)

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry.conversations["conv-no-origin"].origin is None


@pytest.mark.asyncio
async def test_hydrate_sessions_skipped_when_session_registry_is_none():
	"""Callers that don't pass session_registry (or lack the feature) still hydrate
	cleanly - the sessions block is opt-in via the default None parameter."""
	registry = Registry()
	logger = make_logger()
	mock_db = make_firebase_db_mock({
		"sessions": {"sess-1": {"cli_session_id": "sess-1", "state": "idle"}},
	})

	with patch("server.hydration.db", mock_db):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	logger.surface_error.assert_not_called()


def _conv_with_pending(pending):
	node = conv_snapshot(
		"conv-1",
		members={"Claude": member_data(cli_session_id="sess-abc", sender="Claude", alive=True)},
	)
	node["pending_questions"] = pending
	return node


@pytest.mark.asyncio
async def test_hydrate_rebuilds_parked_pending_from_record():
	registry = Registry()
	logger = make_logger()
	snapshot = {
		"conversations": {
			"conv-1": _conv_with_pending({
				"req-1": {
					"sender": "Claude", "questionText": "Deploy?", "cancelled": False,
					"msgId": "m-q", "suggestions": None,
					"cliSessionId": "sess-abc", "askedAt": "2026-07-07T10:00:00+00:00",
				},
			}),
		},
	}
	with patch("server.hydration.db", make_firebase_db_mock(snapshot)):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, None, logger)

	assert registry.pending_count == 1
	assert registry.parked_count == 1
	rec = registry.find_by_request_id("conv-1", "req-1")
	assert rec.future is None
	assert rec.cli_session_id == "sess-abc"
	assert rec.question == "Deploy?"
	assert rec.msg_id == "m-q"
	assert rec.started_at.isoformat() == "2026-07-07T10:00:00+00:00"


@pytest.mark.asyncio
async def test_hydrate_cancels_legacy_record_missing_new_fields_once():
	registry = Registry()
	logger = make_logger()
	backend = MagicMock()
	backend.mark_question_cancelled = AsyncMock()
	snapshot = {
		"conversations": {
			"conv-1": _conv_with_pending({
				"req-legacy": {
					"sender": "Claude", "questionText": "Old?", "cancelled": False,
					"msgId": "m-old", "suggestions": None,
				},
			}),
		},
	}
	with patch("server.hydration.db", make_firebase_db_mock(snapshot)):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, backend, logger)

	assert registry.pending_count == 0
	backend.mark_question_cancelled.assert_awaited_once_with("conv-1", "req-legacy")


@pytest.mark.asyncio
async def test_hydrate_cancels_pending_under_ended_conversation():
	registry = Registry()
	logger = make_logger()
	backend = MagicMock()
	backend.mark_question_cancelled = AsyncMock()
	node = conv_snapshot("conv-9", state="ended")
	node["pending_questions"] = {
		"req-9": {
			"sender": "Claude", "questionText": "Orphan?", "cancelled": False,
			"msgId": "m-9", "suggestions": None,
			"cliSessionId": "sess-9", "askedAt": "2026-07-07T10:00:00+00:00",
		},
	}
	snapshot = {"conversations": {"conv-9": node}}
	with patch("server.hydration.db", make_firebase_db_mock(snapshot)):
		from server.hydration import hydrate_from_firebase
		await hydrate_from_firebase(registry, backend, logger)

	assert registry.pending_count == 0
	backend.mark_question_cancelled.assert_awaited_once_with("conv-9", "req-9")
