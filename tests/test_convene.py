"""Tests for _perform_convene: the convene routing core (Task 4).

Covers routing per session id (add / migrate / skip / already-satisfied),
target resolution (mint "new" vs join an existing conversation), and the
system intro message with skip-reason suffixes. Wake delivery is out of
scope here - _wake_convened is a no-op stub until Task 6.
"""

from __future__ import annotations

import pytest

from server.conversation_ops import _convene_sender_for, _perform_convene
from server.logging_jsonl import JsonlLogger
from server.registry import Conversation, ConversationMember
from server.session_registry import SessionRegistry
from tests.conftest import make_active_conversation, make_registry_with_loopback


@pytest.fixture
def logger(tmp_path):
	return JsonlLogger(str(tmp_path / "log.jsonl"))


@pytest.mark.asyncio
async def test_convene_new_target_gathers_unbound_sessions(logger):
	"""Two unbound sessions, target "new": mints one conversation, both become
	members keyed by cli_session_id, both bound, intro message names both
	senders. Also pins the _convene_sender_for fallback chain: a sender set on
	the record wins; a bare record (no sender, no name) falls back to
	"Agent <first-8-of-id>"; a record with only a name falls back to that."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("cs-alpha-0001", cwd="C:/Work/A")
	session_registry.set_sender("cs-alpha-0001", "Claude Win")
	session_registry.record_session_start("cs-bravo-0002", cwd="C:/Work/B")
	session_registry.record_session_start("cs-carol-0003", cwd="C:/Work/C")
	session_registry.get("cs-carol-0003").name = "Carol"

	# Fallback chain, checked before the convene call so no side effect (member
	# creation calls set_sender) has touched these records yet.
	assert _convene_sender_for(registry, session_registry, "cs-alpha-0001") == "Claude Win"
	assert _convene_sender_for(registry, session_registry, "cs-bravo-0002") == "Agent cs-bravo"
	assert _convene_sender_for(registry, session_registry, "cs-carol-0003") == "Carol"

	cmd = {
		"session_ids": ["cs-alpha-0001", "cs-bravo-0002"],
		"target": "new",
		"title": None,
		"issued_at": "2026-07-06T00:00:00+00:00",
	}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	conv_id = result["conversation_id"]
	assert conv_id is not None
	conv = registry.conversations[conv_id]
	assert set(conv.members_active) == {"cs-alpha-0001", "cs-bravo-0002"}
	assert conv.members_active["cs-alpha-0001"].sender == "Claude Win"
	assert conv.members_active["cs-bravo-0002"].sender == "Agent cs-bravo"
	assert registry.session_to_conversation_id["cs-alpha-0001"] == conv_id
	assert registry.session_to_conversation_id["cs-bravo-0002"] == conv_id
	assert sorted(result["convened"]) == sorted(["Claude Win", "Agent cs-bravo"])
	assert result["skipped"] == []

	intro = conv.messages[-1]
	assert intro["sender"] == "<system>"
	assert intro["type"] == "system"
	assert "John convened:" in intro["text"]
	assert "Claude Win" in intro["text"]
	assert "Agent cs-bravo" in intro["text"]


@pytest.mark.asyncio
async def test_convene_into_existing_conversation(logger):
	"""target = an existing active conversation id: the convened session
	becomes a member of that conversation, not a freshly minted one."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-host", cwd="C:/Work/H")
	session_registry.set_sender("s-host", "Host")
	session_registry.record_session_start("s-guest", cwd="C:/Work/G")
	session_registry.set_sender("s-guest", "Guest")

	conv = make_active_conversation(
		conversation_id="conv-host", member_session_id="s-host", sender="Host", cwd="C:/Work/H",
	)
	registry.conversations["conv-host"] = conv
	registry.bind_session("s-host", "conv-host")

	cmd = {"session_ids": ["s-guest"], "target": "conv-host", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["conversation_id"] == "conv-host"
	assert result["convened"] == ["Guest"]
	assert result["skipped"] == []
	assert "s-guest" in conv.members_active
	assert registry.session_to_conversation_id["s-guest"] == "conv-host"
	assert len(conv.members_active) == 2


@pytest.mark.asyncio
async def test_convene_migrates_solo_member(logger):
	"""A session bound to a conversation where it is the only alive member is
	migrated to the target; the (now-empty) source conversation ends."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-solo", cwd="C:/Work/S")
	session_registry.set_sender("s-solo", "Solo")
	session_registry.record_session_start("s-host", cwd="C:/Work/H")
	session_registry.set_sender("s-host", "Host")

	solo = make_active_conversation(
		conversation_id="conv-solo", member_session_id="s-solo", sender="Solo", cwd="C:/Work/S",
	)
	registry.conversations["conv-solo"] = solo
	registry.bind_session("s-solo", "conv-solo")

	target = make_active_conversation(
		conversation_id="conv-target", member_session_id="s-host", sender="Host", cwd="C:/Work/H",
	)
	registry.conversations["conv-target"] = target
	registry.bind_session("s-host", "conv-target")

	cmd = {"session_ids": ["s-solo"], "target": "conv-target", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["conversation_id"] == "conv-target"
	assert result["convened"] == ["Solo"]
	assert result["skipped"] == []
	assert registry.conversations["conv-solo"].state == "ended"
	assert "s-solo" not in registry.conversations["conv-solo"].members_active
	assert "s-solo" in target.members_active
	assert registry.session_to_conversation_id["s-solo"] == "conv-target"


@pytest.mark.asyncio
async def test_convene_skips_multi_party_member(logger):
	"""A conversation with two alive members: convening one of them is skipped
	as "in a multi-party conversation"; membership is unchanged."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-1", cwd="C:/Work/1")
	session_registry.set_sender("s-1", "One")
	session_registry.record_session_start("s-2", cwd="C:/Work/2")
	session_registry.set_sender("s-2", "Two")

	conv = make_active_conversation(
		conversation_id="conv-multi", member_session_id="s-1", sender="One", cwd="C:/Work/1",
	)
	member_two = ConversationMember(
		cli_session_id="s-2", sender="Two", cwd="C:/Work/2", surface="windows", joined_at=0.0,
	)
	conv.members_active["s-2"] = member_two
	registry.conversations["conv-multi"] = conv
	registry.bind_session("s-1", "conv-multi")
	registry.bind_session("s-2", "conv-multi")

	cmd = {"session_ids": ["s-1"], "target": "new", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["convened"] == []
	assert result["skipped"] == [{"session_id": "s-1", "reason": "in a multi-party conversation"}]
	assert "s-1" in conv.members_active
	assert registry.session_to_conversation_id["s-1"] == "conv-multi"
	assert len(conv.members_active) == 2


@pytest.mark.asyncio
async def test_convene_new_target_all_skipped_mints_no_orphan(logger):
	"""A live session bound to a multi-party conversation, convened with
	target="new", is skipped - and lazy mint means NO orphan conversation is
	created (the reachable all-skipped case)."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry
	# pre-existing multi-party conversation with two alive members
	conv_mp = make_active_conversation(conversation_id="conv-mp", member_session_id="s-1", sender="One", cwd="C:/Work/1")
	conv_mp.members_active["s-2"] = ConversationMember(
		cli_session_id="s-2", sender="Two", cwd="C:/Work/2", surface="windows", joined_at=0.0,
	)
	registry.conversations["conv-mp"] = conv_mp
	registry.bind_session("s-1", "conv-mp")
	registry.bind_session("s-2", "conv-mp")
	session_registry.record_session_start("s-1", cwd="C:/Work/1")
	session_registry.record_session_start("s-2", cwd="C:/Work/2")

	cmd = {"session_ids": ["s-1"], "target": "new", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["convened"] == []
	assert result["skipped"] == [{"session_id": "s-1", "reason": "in a multi-party conversation"}]
	assert result["conversation_id"] is None
	assert set(registry.conversations) == {"conv-mp"}  # no orphan conv-<uuid> minted


@pytest.mark.asyncio
async def test_convene_already_in_target_counts_as_convened(logger):
	"""A session already a member of the target conversation counts as
	convened (already-satisfied = success): no duplicate member, no error."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-member", cwd="C:/Work/M")
	session_registry.set_sender("s-member", "Member")

	conv = make_active_conversation(
		conversation_id="conv-x", member_session_id="s-member", sender="Member", cwd="C:/Work/M",
	)
	registry.conversations["conv-x"] = conv
	registry.bind_session("s-member", "conv-x")
	existing_member = conv.members_active["s-member"]

	cmd = {"session_ids": ["s-member"], "target": "conv-x", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["conversation_id"] == "conv-x"
	assert result["convened"] == ["Member"]
	assert result["skipped"] == []
	assert len(conv.members_active) == 1
	assert conv.members_active["s-member"] is existing_member


@pytest.mark.asyncio
async def test_convene_skips_terminal_sessions(logger):
	"""A session_registry record in a terminal state ("ended") is skipped as
	"not a live session" and never joins the target."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-dead", cwd="C:/Work/D")
	session_registry.record_session_end("s-dead", reason="logout", ended_at="2026-07-06T00:00:00+00:00")

	cmd = {"session_ids": ["s-dead"], "target": "new", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result["convened"] == []
	assert result["skipped"] == [{"session_id": "s-dead", "reason": "not a live session"}]
	assert "s-dead" not in registry.session_to_conversation_id
	assert result["conversation_id"] is None
	assert registry.conversations == {}  # lazy mint: an all-skipped "new" convene creates no orphan


@pytest.mark.asyncio
async def test_convene_intro_message_and_skip_reasons(logger):
	"""The system intro message names convened senders and appends a skip
	suffix (id-prefix + reason) when any session was skipped."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-ok1", cwd="C:/Work/1")
	session_registry.set_sender("s-ok1", "Ok One")
	session_registry.record_session_start("s-ok2", cwd="C:/Work/2")
	session_registry.set_sender("s-ok2", "Ok Two")
	session_registry.record_session_start("s-dead", cwd="C:/Work/D")
	session_registry.record_session_end("s-dead", reason="logout", ended_at="2026-07-06T00:00:00+00:00")

	cmd = {
		"session_ids": ["s-ok1", "s-ok2", "s-dead"],
		"target": "new",
		"title": None,
		"issued_at": "x",
	}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	conv = registry.conversations[result["conversation_id"]]
	intro = conv.messages[-1]["text"]
	assert intro.startswith("John convened: ")
	assert "Ok One" in intro
	assert "Ok Two" in intro
	assert "(skipped: s-dead - not a live session)" in intro


@pytest.mark.asyncio
async def test_convene_invalid_target_returns_error(logger):
	"""An unknown or non-Active target skips every requested session with
	"target not found or not Active" and returns conversation_id=None."""
	registry = make_registry_with_loopback()
	session_registry = SessionRegistry()
	registry.sessions = session_registry

	session_registry.record_session_start("s-a", cwd="C:/Work/A")
	session_registry.record_session_start("s-b", cwd="C:/Work/B")

	cmd = {"session_ids": ["s-a", "s-b"], "target": "conv-nope", "title": None, "issued_at": "x"}
	result = await _perform_convene(registry, session_registry, cmd, logger)

	assert result == {
		"conversation_id": None,
		"convened": [],
		"skipped": [
			{"session_id": "s-a", "reason": "target not found or not Active"},
			{"session_id": "s-b", "reason": "target not found or not Active"},
		],
	}
	assert "s-a" not in registry.session_to_conversation_id
	assert "s-b" not in registry.session_to_conversation_id

	# Also covers an existing-but-ended target, not just an unknown id.
	ended_conv = Conversation(id="conv-ended", title="ended")
	ended_conv.state = "ended"
	registry.conversations["conv-ended"] = ended_conv
	cmd2 = {"session_ids": ["s-a"], "target": "conv-ended", "title": None, "issued_at": "x"}
	result2 = await _perform_convene(registry, session_registry, cmd2, logger)
	assert result2["conversation_id"] is None
	assert result2["skipped"] == [{"session_id": "s-a", "reason": "target not found or not Active"}]
