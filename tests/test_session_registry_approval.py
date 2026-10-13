"""The session record's open-dialog mark (awaiting_approval, approval_since)."""

from server.session_registry import SessionRegistry


def _mirror(sessions):
	writes = []
	sessions.set_mirror(lambda sid, payload: writes.append((sid, payload)))
	return writes


def test_set_awaiting_approval_marks_and_clears():
	sessions = SessionRegistry()
	writes = _mirror(sessions)
	sessions.set_awaiting_approval("s1", "2026-10-12T10:00:00+00:00")
	rec = sessions.get("s1")
	assert rec.awaiting_approval is True
	assert rec.approval_since == "2026-10-12T10:00:00+00:00"
	sessions.set_awaiting_approval("s1", None)
	assert rec.awaiting_approval is False
	assert rec.approval_since is None
	assert [payload["awaiting_approval"] for _, payload in writes] == [True, False]


def test_an_ended_session_is_never_marked():
	sessions = SessionRegistry()
	sessions.record_session_start("s1", cwd="C:/Work/X")
	sessions.record_session_end("s1", reason="other", ended_at="2026-10-12T10:00:00+00:00")
	sessions.set_awaiting_approval("s1", "2026-10-12T10:01:00+00:00")
	assert sessions.get("s1").awaiting_approval is False


def test_hydration_clears_a_stored_open_dialog_and_writes_it_back():
	sessions = SessionRegistry()
	writes = _mirror(sessions)
	sessions.hydrate_record({
		"cli_session_id": "s1", "state": "active", "last_event_at": "2026-10-12T10:00:00+00:00",
		"awaiting_approval": True, "approval_since": "2026-10-12T10:00:00+00:00",
	})
	rec = sessions.get("s1")
	assert rec.awaiting_approval is False
	assert rec.approval_since is None
	assert len(writes) == 1
	assert writes[0][1]["awaiting_approval"] is False


def test_hydration_of_a_record_with_no_open_dialog_writes_nothing():
	sessions = SessionRegistry()
	writes = _mirror(sessions)
	sessions.hydrate_record({"cli_session_id": "s1", "state": "idle", "last_event_at": "2026-10-12T10:00:00+00:00"})
	assert writes == []
