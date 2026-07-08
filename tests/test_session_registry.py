"""SessionRegistry unit tests - pure in-memory, no backend."""

from server.session_registry import (
	SessionRecord,
	SessionRegistry,
	map_hook_event_to_state,
)


def _reg():
	return SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")


def test_session_start_upserts_idle_record():
	reg = _reg()
	rec = reg.record_session_start("sess-A", cwd="C:/Work/X", start_source="startup")
	assert rec.state == "idle"
	assert rec.cwd == "C:/Work/X"
	assert rec.surface == "windows"
	assert rec.started_at == "2026-07-06T12:00:00+00:00"
	assert reg.get("sess-A") is rec

def test_hook_event_mapping():
	assert map_hook_event_to_state("UserPromptSubmit", "thinking") == "active"
	assert map_hook_event_to_state("PostToolUse", "thinking") == "active"
	assert map_hook_event_to_state("Stop", "clear") == "idle"
	assert map_hook_event_to_state("PreToolUse", "clear") == "awaiting_human"
	assert map_hook_event_to_state("PreToolUse", "waiting") == "awaiting_agent"
	assert map_hook_event_to_state("PreToolUse", "tool:Bash") == "active"
	assert map_hook_event_to_state("SomethingElse", "x") is None

def test_unknown_session_hook_event_discovers_record():
	reg = _reg()
	rec = reg.upsert_from_hook("sess-B", state="active", detail="build.ps1")
	assert rec.source == "hook"
	assert rec.state == "active"
	assert rec.state_detail == "build.ps1"
	assert rec.last_event_at == "2026-07-06T12:00:00+00:00"

def test_mcp_touch_enriches_cwd_and_sender():
	reg = _reg()
	reg.upsert_from_hook("sess-C", state="active")
	rec = reg.touch_mcp("sess-C", cwd="/home/john/work/x", sender="Claude WSL")
	assert rec.cwd == "/home/john/work/x"
	assert rec.surface == "wsl"
	assert rec.sender == "Claude WSL"

def test_mark_wait_cancelled_resets_state():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.upsert_from_hook("sess-A", state="awaiting_agent", event="PreToolUse")
	reg.mark_wait_cancelled("sess-A")
	rec = reg.get("sess-A")
	assert rec.state == "active"
	assert rec.state_detail == "wait-cancelled"
	assert rec.last_transition_source == "cancel"
	assert rec.last_event_at == "2026-07-06T12:00:00+00:00"
	reg.mark_wait_cancelled("sess-UNKNOWN")  # no raise

def test_session_end_marks_ended():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	rec = reg.record_session_end("sess-A", reason="logout", ended_at="2026-07-06T13:00:00+00:00")
	assert rec.state == "ended"
	assert rec.end_reason == "logout"
	assert reg.record_session_end("sess-GONE", reason="logout", ended_at="x") is None

def test_rings_enrich_and_discover():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.apply_rings({
		"sess-A": {"pct": 41.5, "model": "opus", "status": "ok"},
		"sess-NEW": {"pct": 10.0, "model": "sonnet", "status": "ok"},
	})
	assert reg.get("sess-A").context_pct == 41.5
	assert reg.get("sess-A").model == "opus"
	assert reg.get("sess-NEW").source == "rings"

def test_binding_and_sender_setters():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.set_binding("sess-A", "conv-1")
	assert reg.get("sess-A").conversation_id == "conv-1"
	reg.set_binding("sess-A", None)
	assert reg.get("sess-A").conversation_id is None
	reg.set_binding("sess-UNKNOWN", "conv-2")
	assert reg.get("sess-UNKNOWN") is None
	reg.set_sender("sess-A", "Claude Win 2")
	assert reg.get("sess-A").sender == "Claude Win 2"
	reg.set_sender("sess-UNKNOWN", "X")
	assert reg.get("sess-UNKNOWN") is None

def test_mirror_fires_on_change_only():
	calls = []
	reg = _reg()
	reg.set_mirror(lambda sid, payload: calls.append((sid, payload)))
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	n = len(calls)
	assert n >= 1
	reg.apply_rings({"sess-A": {"pct": 41.5, "model": "opus", "status": "ok"}})
	assert len(calls) == n + 1
	reg.apply_rings({"sess-A": {"pct": 41.5, "model": "opus", "status": "ok"}})
	assert len(calls) == n + 1  # identical payload, no re-fire

def test_sweep_marks_lost_and_prunes():
	reg = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	reg.record_session_start("sess-BLOCKED", cwd="C:/Work/Y")
	reg.upsert_from_hook("sess-BLOCKED", state="awaiting_human")
	reg.record_session_start("sess-RINGING", cwd="C:/Work/Z")
	now_ts = 1782648000.0  # arbitrary epoch; only deltas matter
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	pruned = reg.sweep(
		now_ts=last + 1000,
		lost_after_seconds=900,
		retention_seconds=72 * 3600,
		rings_fresh=True,
		ring_ids={"sess-RINGING"},
	)
	assert reg.get("sess-OLD").state == "lost"
	assert reg.get("sess-BLOCKED").state == "awaiting_human"  # blocked exemption
	assert reg.get("sess-RINGING").state == "idle"            # ring is liveness
	assert pruned == []

def test_sweep_suspends_lost_marking_when_sensor_offline():
	reg = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	reg.sweep(now_ts=last + 10000, lost_after_seconds=900, retention_seconds=999999,
		rings_fresh=False, ring_ids=set())
	assert reg.get("sess-OLD").state == "idle"

def test_sweep_prunes_terminal_records_past_retention():
	reg = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.record_session_end("sess-A", reason="logout", ended_at="2026-07-06T12:00:00+00:00")
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	pruned = reg.sweep(now_ts=last + 73 * 3600, lost_after_seconds=900,
		retention_seconds=72 * 3600, rings_fresh=True, ring_ids=set())
	assert pruned == ["sess-A"]
	assert reg.get("sess-A") is None

def test_revival_after_lost():
	reg = _reg()
	rec = reg.record_session_start("sess-A", cwd="C:/Work/X")
	rec.state = "lost"
	reg.upsert_from_hook("sess-A", state="active")
	assert reg.get("sess-A").state == "active"

def test_queue_and_pop_notices():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	assert reg.queue_notice("sess-A", "You have been convened into conv-1.") is True
	assert reg.queue_notice("sess-UNKNOWN", "x") is False
	assert reg.get("sess-A").pending_notices == ["You have been convened into conv-1."]
	assert reg.pop_notices("sess-A") == ["You have been convened into conv-1."]
	assert reg.get("sess-A").pending_notices == []
	assert reg.pop_notices("sess-A") == []
	assert reg.pop_notices("sess-UNKNOWN") == []

def test_notices_fire_mirror_and_hydrate():
	calls = []
	reg = _reg()
	reg.set_mirror(lambda sid, payload: calls.append(payload))
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.queue_notice("sess-A", "n1")
	assert calls[-1]["pending_notices"] == ["n1"]
	reg2 = _reg()
	reg2.hydrate_record(calls[-1])
	assert reg2.get("sess-A").pending_notices == ["n1"]

def test_cwd_fill_only_when_empty():
	reg = _reg()
	reg.upsert_from_hook("sess-B", state="active", cwd="C:/Work/Y")
	assert reg.get("sess-B").cwd == "C:/Work/Y"
	assert reg.get("sess-B").surface == "windows"
	reg.upsert_from_hook("sess-B", state="idle", cwd="C:/OTHER")
	assert reg.get("sess-B").cwd == "C:/Work/Y"  # SessionStart/MCP stay authoritative

def test_last_transition_source():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	assert reg.get("sess-A").last_transition_source == "session_start"
	reg.upsert_from_hook("sess-A", state="active", event="UserPromptSubmit")
	assert reg.get("sess-A").last_transition_source == "hook:UserPromptSubmit"
	reg.record_session_end("sess-A", reason="logout", ended_at="2026-07-06T13:00:00+00:00")
	assert reg.get("sess-A").last_transition_source == "session_end"

def test_apply_rings_copies_name():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.apply_rings({"sess-A": {"pct": 10.0, "model": "opus", "status": "ok",
		"name": "Fixing FCM tests", "name_source": "ai-title"}})
	rec = reg.get("sess-A")
	assert rec.name == "Fixing FCM tests"
	assert rec.name_source == "ai-title"
	assert rec.last_transition_source == "session_start"  # apply_rings must not touch it

def test_resume_sentinel_detects_id_change():
	clock = [1000.0]
	reg = SessionRegistry(now=lambda: "2026-07-07T12:00:00+00:00", mono=lambda: clock[0])
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	reg.record_session_end("sess-OLD", reason="other", ended_at="2026-07-07T12:01:00+00:00")
	reg.note_spawn_resume("sess-OLD", "C:/Work/X")
	# same id returning: no record change signal (record exists; entry stays queued)
	assert reg.check_resume_id_change("sess-OLD", "C:/Work/X") is None
	# unknown id in the same cwd right after a resume: the tripwire (pops the entry)
	assert reg.check_resume_id_change("sess-NEW", "C:/Work/X") == "sess-OLD"
	# entry popped: second check is quiet
	assert reg.check_resume_id_change("sess-NEW2", "C:/Work/X") is None

def test_resume_sentinel_expires_and_ignores_other_cwds():
	clock = [1000.0]
	reg = SessionRegistry(now=lambda: "2026-07-07T12:00:00+00:00", mono=lambda: clock[0])
	reg.note_spawn_resume("sess-OLD", "C:/Work/X")
	assert reg.check_resume_id_change("sess-NEW", "C:/Work/OTHER") is None
	clock[0] += 200.0  # past the 180s TTL
	assert reg.check_resume_id_change("sess-NEW", "C:/Work/X") is None

def test_in_tool_and_star_derive_blocked_on_approval():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.upsert_from_hook("sess-A", state="active", detail="Bash: git status", event="PreToolUse", in_tool=True)
	assert reg.get("sess-A").in_tool is True
	assert reg.get("sess-A").blocked_on_approval is False
	reg.apply_rings({"sess-A": {"pct": 0.1, "model": "m", "status": "ok", "title_state": "star"}})
	rec = reg.get("sess-A")
	assert rec.title_state == "star"
	assert rec.blocked_on_approval is True
	assert rec.state == "active"  # apply_rings never changes state

def test_post_tool_use_clears_blocked_on_approval():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.upsert_from_hook("sess-A", state="active", detail="Bash: x", event="PreToolUse", in_tool=True)
	reg.apply_rings({"sess-A": {"pct": 0.1, "title_state": "star"}})
	assert reg.get("sess-A").blocked_on_approval is True
	reg.upsert_from_hook("sess-A", state="active", detail=None, event="PostToolUse", in_tool=False)
	assert reg.get("sess-A").in_tool is False
	assert reg.get("sess-A").blocked_on_approval is False

def test_working_ring_clears_blocked_on_approval():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.upsert_from_hook("sess-A", state="active", detail="Bash: x", event="PreToolUse", in_tool=True)
	reg.apply_rings({"sess-A": {"pct": 0.1, "title_state": "star"}})
	reg.apply_rings({"sess-A": {"pct": 0.1, "title_state": "working"}})
	rec = reg.get("sess-A")
	assert rec.title_state == "working"
	assert rec.blocked_on_approval is False

def test_sensing_fields_hydrate():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.upsert_from_hook("sess-A", state="active", detail="Bash: x", event="PreToolUse", in_tool=True)
	reg.apply_rings({"sess-A": {"pct": 0.1, "title_state": "star"}})
	payload = reg.get("sess-A").to_payload()
	reg2 = _reg()
	reg2.hydrate_record(payload)
	rec = reg2.get("sess-A")
	assert rec.title_state == "star" and rec.in_tool is True and rec.blocked_on_approval is True

def test_record_session_end_clears_blocked_on_approval():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="active", detail="Bash: x", event="PreToolUse", in_tool=True)
	reg.apply_rings({"s1": {"pct": 0.1, "title_state": "star"}})
	assert reg.get("s1").blocked_on_approval is True
	reg.record_session_end("s1", reason="exit", ended_at="2026-07-06T12:00:00+00:00")
	rec = reg.get("s1")
	assert rec.state == "ended"
	assert rec.in_tool is False
	assert rec.blocked_on_approval is False

def test_sweep_lost_clears_blocked_on_approval():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="active", detail="Bash: x", event="PreToolUse", in_tool=True)
	reg.apply_rings({"s1": {"pct": 0.1, "title_state": "star"}})
	assert reg.get("s1").blocked_on_approval is True
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	reg.sweep(now_ts=last + 1000, lost_after_seconds=900, retention_seconds=72 * 3600,
		rings_fresh=True, ring_ids=set())
	rec = reg.get("s1")
	assert rec.state == "lost"
	assert rec.in_tool is False
	assert rec.blocked_on_approval is False
