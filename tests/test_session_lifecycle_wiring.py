"""Tests for Registry <-> SessionRegistry wiring: bind/unbind write-through,
session-end record marking, and sender learning via membership paths."""

import pytest

from server.registry import Registry
from server.session_registry import SessionRegistry


def test_bind_unbind_write_through_to_session_record():
	r = Registry()
	s = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	r.sessions = s
	s.record_session_start("sess-A", cwd="C:/Work/X")
	r.bind_session("sess-A", "conv-1")
	assert s.get("sess-A").conversation_id == "conv-1"
	r.unbind_session("sess-A")
	assert s.get("sess-A").conversation_id is None


def test_bind_without_sessions_ref_still_works():
	r = Registry()
	r.bind_session("sess-A", "conv-1")
	assert r.session_to_conversation_id["sess-A"] == "conv-1"


@pytest.mark.anyio
async def test_session_end_marks_registry_record():
	from server.cli_session_end import handle_session_end
	r = Registry()
	s = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	r.sessions = s
	s.record_session_start("sess-A", cwd="C:/Work/X")
	await handle_session_end(
		registry=r, session_id="sess-A", reason="logout",
		now=lambda: "2026-07-06T13:00:00+00:00", session_registry=s,
	)
	rec = s.get("sess-A")
	assert rec.state == "ended"
	assert rec.end_reason == "logout"


@pytest.mark.anyio
async def test_add_member_sets_session_sender():
	"""After _add_member on a registry with sessions attached and a started
	record, the session record's sender equals the member's disambiguated
	sender (learned via the membership path, not the decorator)."""
	from server.conversation_ops import _add_member
	from server.registry import Conversation

	r = Registry()
	s = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	r.sessions = s
	s.record_session_start("sess-A", cwd="C:/Work/X")

	conv = Conversation(id="conv-1", title="test")
	r.conversations["conv-1"] = conv

	await _add_member(r, "conv-1", "sess-A", "Claude", "C:/Work/X")

	member = conv.members_active["sess-A"]
	assert s.get("sess-A").sender == member.sender
