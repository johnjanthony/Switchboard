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
	reg.apply_telemetry("sess-A", {"model": "opus", "effort": "high"})
	assert len(calls) == n + 1
	reg.apply_telemetry("sess-A", {"model": "opus", "effort": "high"})
	assert len(calls) == n + 1  # identical payload, no re-fire

def test_sweep_marks_lost_and_prunes():
	reg = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	reg.record_session_start("sess-OLD", cwd="C:/Work/X")
	reg.record_session_start("sess-BLOCKED", cwd="C:/Work/Y")
	reg.upsert_from_hook("sess-BLOCKED", state="awaiting_human")
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	pruned = reg.sweep(
		now_ts=last + 1000,
		lost_after_seconds=900,
		poll_lost_after_seconds=90,
		retention_seconds=72 * 3600,
	)
	assert reg.get("sess-OLD").state == "lost"
	assert reg.get("sess-BLOCKED").state == "awaiting_human"  # blocked exemption
	assert pruned == []

def test_sweep_prunes_terminal_records_past_retention():
	reg = SessionRegistry(now=lambda: "2026-07-06T12:00:00+00:00")
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.record_session_end("sess-A", reason="logout", ended_at="2026-07-06T12:00:00+00:00")
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	pruned = reg.sweep(now_ts=last + 73 * 3600, lost_after_seconds=900,
		retention_seconds=72 * 3600, poll_lost_after_seconds=90)
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

def _sweep(reg, **overrides):
	import datetime as _dt
	last = _dt.datetime.fromisoformat("2026-07-06T12:00:00+00:00").timestamp()
	args = dict(now_ts=last + 1000, lost_after_seconds=900, retention_seconds=72 * 3600,
		poll_lost_after_seconds=90)
	args.update(overrides)
	return reg.sweep(**args)

def test_awaiting_human_without_live_pending_goes_lost():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="awaiting_human", detail=None, event="PreToolUse")
	_sweep(reg, live_ask_ids=set(), live_wait_ids=set())
	assert reg.get("s1").state == "lost"

def test_awaiting_human_with_live_pending_stays_exempt():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="awaiting_human", detail=None, event="PreToolUse")
	_sweep(reg, live_ask_ids={"s1"}, live_wait_ids=set())
	assert reg.get("s1").state == "awaiting_human"

def test_awaiting_agent_with_live_wait_entry_stays_exempt():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="awaiting_agent", detail=None, event="PreToolUse")
	_sweep(reg, live_ask_ids=set(), live_wait_ids={"s1"})
	assert reg.get("s1").state == "awaiting_agent"
	_sweep(reg, live_ask_ids=set(), live_wait_ids=set())
	assert reg.get("s1").state == "lost"

def test_no_liveness_info_preserves_blanket_exemption():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="awaiting_human", detail=None, event="PreToolUse")
	_sweep(reg)
	assert reg.get("s1").state == "awaiting_human"

def test_late_hook_event_does_not_resurrect_ended_record():
	# REV-114: a straggler /agent_status POST (Stop hook racing the SessionEnd
	# marker sweep) must not flip an ended record back to live - that hides the
	# phone Resume affordance until the sweeper falsely re-marks it lost.
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.record_session_end("sess-A", reason="logout", ended_at="2026-07-06T11:59:00+00:00")
	rec = reg.upsert_from_hook("sess-A", state="idle", event="Stop")
	assert rec.state == "ended"
	assert rec.end_reason == "logout"
	assert rec.last_event_at == "2026-07-06T11:59:00+00:00"


def test_hook_event_still_revives_lost_record():
	# Complement of the ended-guard: "lost" is the sweeper's presumption, and a
	# live hook event is proof of life (pins decided semantics alongside the
	# existing test_revival_after_lost).
	reg = _reg()
	rec = reg.record_session_start("sess-A", cwd="C:/Work/X")
	rec.state = "lost"
	rec.end_reason = "presumed-dead"
	out = reg.upsert_from_hook("sess-A", state="active")
	assert out.state == "active"
	assert out.end_reason is None


def test_sweep_presumed_dead_increments_counter():
	import datetime as _dt
	reg = SessionRegistry(now=lambda: "2026-07-14T12:00:00+00:00")
	reg.record_session_start("sess-pd-1", cwd="C:/Work/X")
	last = _dt.datetime.fromisoformat("2026-07-14T12:00:00+00:00").timestamp()
	reg.sweep(
		now_ts=last + 1000, lost_after_seconds=900, retention_seconds=72 * 3600,
		poll_lost_after_seconds=90,
	)
	assert reg.get("sess-pd-1").state == "lost"
	assert reg.presumed_dead_total == 1


def test_marker_health_check_fires_once_at_threshold():
	reg = SessionRegistry()
	reg.presumed_dead_total = 3
	assert reg.marker_health_check() is True
	assert reg.marker_health_check() is False


def test_marker_health_check_quiet_below_threshold():
	reg = SessionRegistry()
	reg.presumed_dead_total = 2
	assert reg.marker_health_check() is False


def test_marker_health_check_quiet_when_markers_applied():
	reg = SessionRegistry()
	reg.presumed_dead_total = 10
	reg.markers_applied_total = 1
	assert reg.marker_health_check() is False


def test_upsert_from_hook_sets_cli():
	reg = SessionRegistry()
	rec = reg.upsert_from_hook("s1", state="active", cwd="C:/w", event="UserPromptSubmit", cli="antigravity")
	assert rec.cli == "antigravity"


def test_upsert_from_hook_cli_none_preserves_existing():
	reg = SessionRegistry()
	reg.upsert_from_hook("s1", state="active", cli="antigravity")
	rec = reg.upsert_from_hook("s1", state="idle")
	assert rec.cli == "antigravity"


def test_record_spawn_choice_sets_fields_and_mirrors():
	reg = SessionRegistry(now=lambda: "2026-07-30T00:00:00Z")
	fired = []
	reg.set_mirror(lambda sid, payload: fired.append((sid, payload)))
	rec = reg.record_spawn_choice("sid-choice", model="haiku", effort=None)
	assert rec.spawn_model == "haiku"
	assert rec.spawn_effort is None
	assert fired and fired[-1][0] == "sid-choice"
	assert fired[-1][1]["spawn_model"] == "haiku"


def test_session_start_preserves_spawn_choice():
	reg = SessionRegistry(now=lambda: "2026-07-30T00:00:00Z")
	reg.record_spawn_choice("sid-choice", model="sonnet", effort="low")
	reg.record_session_start("sid-choice", cwd="C:/Work/X")
	rec = reg.get("sid-choice")
	assert rec.spawn_model == "sonnet"
	assert rec.spawn_effort == "low"


def test_hydrate_record_restores_spawn_choice():
	reg = SessionRegistry()
	reg.hydrate_record({
		"cli_session_id": "sid-hyd",
		"cwd": "C:/Work/X",
		"state": "idle",
		"spawn_model": "haiku",
		"spawn_effort": None,
	})
	rec = reg.get("sid-hyd")
	assert rec.spawn_model == "haiku"
	assert rec.spawn_effort is None


def test_record_spawn_choice_seeds_cwd_on_new_record():
	# A spawn choice arrives for a brand new session id, so _ensure creates a
	# stub with an empty cwd. Seeding it here is what keeps the board row from
	# reading "(unknown)" until SessionStart fills the record in for real.
	reg = SessionRegistry(now=lambda: "2026-07-30T00:00:00Z")
	rec = reg.record_spawn_choice("sid-new", model="opus", effort="high", cwd="C:/Work/X")
	assert rec.cwd == "C:/Work/X"
	assert rec.surface == "windows"


def test_record_spawn_choice_does_not_clobber_existing_cwd():
	# An established record's own cwd wins - a later spawn choice for the same
	# id must not overwrite it with a different value.
	reg = SessionRegistry(now=lambda: "2026-07-30T00:00:00Z")
	reg.record_session_start("sid-existing", cwd="C:/Work/Established")
	rec = reg.record_spawn_choice("sid-existing", model="opus", effort="high", cwd="C:/Work/Other")
	assert rec.cwd == "C:/Work/Established"
	assert rec.surface == "windows"


def test_effort_change_alone_fires_mirror():
	# /effort mid-session changes only effort; the chip must still update.
	calls = []
	reg = _reg()
	reg.set_mirror(lambda sid, payload: calls.append(payload))
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	reg.apply_telemetry("sess-A", {"model": "claude-opus-5-5", "effort": "high"})
	n = len(calls)
	reg.apply_telemetry("sess-A", {"model": "claude-opus-5-5", "effort": "xhigh"})
	assert len(calls) == n + 1
	assert calls[-1]["model_label"] == "Opus 5.5 · xhigh"


def test_to_payload_resolves_spawn_pick_then_observation():
	reg = _reg()
	reg.record_spawn_choice("sess-A", model="sonnet", effort="medium", cwd="C:/Work/X")
	payload = reg.get("sess-A").to_payload()
	assert payload["model_label"] == "Sonnet · medium"
	assert payload["model_source"] == "spawn"
	reg.apply_telemetry("sess-A", {"model": "claude-sonnet-5-5", "effort": "medium"})
	payload = reg.get("sess-A").to_payload()
	assert payload["model_label"] == "Sonnet 5.5 · medium"
	assert payload["model_source"] == "observed"


def test_to_payload_without_model_or_pick_has_no_label():
	reg = _reg()
	reg.record_session_start("sess-A", cwd="C:/Work/X")
	payload = reg.get("sess-A").to_payload()
	assert payload["model_label"] is None
	assert payload["model_source"] is None


def test_hydrate_record_restores_effort_and_recomputes_label():
	reg = SessionRegistry()
	reg.hydrate_record({
		"cli_session_id": "sid-hyd",
		"cwd": "C:/Work/X",
		"state": "idle",
		"model": "claude-opus-5-5",
		"effort": "high",
		"model_label": "stale label",
		"model_source": "spawn",
	})
	rec = reg.get("sid-hyd")
	assert rec.effort == "high"
	payload = rec.to_payload()
	assert payload["model_label"] == "Opus 5.5 · high"
	assert payload["model_source"] == "observed"


def test_an_old_record_hydrates_without_the_approval_marker():
	# Records mirrored before the marker was retired still carry title_state and
	# blocked_on_approval; hydration drops both and the next write omits them.
	reg = _reg()
	reg.hydrate_record({
		"cli_session_id": "sess-A", "cwd": "C:/Work/X", "state": "active",
		"in_tool": True, "title_state": "star", "blocked_on_approval": True,
	})
	payload = reg.get("sess-A").to_payload()
	assert payload["in_tool"] is True
	assert "title_state" not in payload and "blocked_on_approval" not in payload
