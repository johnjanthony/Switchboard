"""Telemetry reports from the Claude Code mod: validation and the session
fields they write."""

from server.session_registry import SessionRegistry
from server.telemetry import parse_telemetry


def _reg():
	return SessionRegistry(now=lambda: "2026-10-10T12:00:00+00:00")


def test_parse_keeps_a_valid_report():
	report = parse_telemetry({
		"context": {"tokens": 412000, "window": 1000000, "percent": 41},
		"rate_limits": [
			{"kind": "five_hour", "percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"},
			{"kind": "seven_day", "percent_used": 61},
		],
		"model": "claude-opus-5-5",
		"effort": "high",
		"title": {"name": "Watchtower metrics reads", "source": "ai-title"},
	})
	assert report == {
		"context": {"tokens": 412000, "window": 1000000},
		"rate_limits": [
			{"kind": "five_hour", "percent_used": 23.5, "resets_at": "2026-10-10T18:00:00Z"},
			{"kind": "seven_day", "percent_used": 61, "resets_at": None},
		],
		"model": "claude-opus-5-5",
		"effort": "high",
		"title": {"name": "Watchtower metrics reads", "source": "ai-title"},
	}


def test_parse_drops_each_bad_field_and_keeps_the_rest():
	report = parse_telemetry({
		"context": {"tokens": 5, "window": 0},
		"rate_limits": [
			{"kind": "five_hour", "percent_used": 150},
			{"kind": "spend_limit", "percent_used": 10},
			{"kind": "seven_day", "percent_used": 40, "resets_at": "not a time"},
		],
		"model": "",
		"effort": "turbo",
		"title": {"name": "x", "source": "filename"},
		"unknown": 1,
	})
	assert report == {"rate_limits": [{"kind": "seven_day", "percent_used": 40, "resets_at": None}]}


def test_parse_drops_negative_tokens_but_keeps_the_window():
	assert parse_telemetry({"context": {"tokens": -1, "window": 200000}}) == {"context": {"tokens": None, "window": 200000}}


def test_parse_rejects_booleans_posing_as_numbers():
	assert parse_telemetry({"context": {"window": True}, "rate_limits": [{"kind": "five_hour", "percent_used": False}]}) == {}


def test_parse_keeps_a_null_effort_as_a_clear():
	assert parse_telemetry({"model": "claude-haiku-5-5", "effort": None}) == {"model": "claude-haiku-5-5", "effort": None}


def test_parse_ignores_a_body_that_is_not_an_object():
	assert parse_telemetry(["context"]) == {}
	assert parse_telemetry(None) == {}


def test_context_sets_tokens_window_and_a_fractional_pct():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	rec = reg.apply_telemetry("s1", {"context": {"tokens": 412000, "window": 1000000}})
	assert (rec.context_tokens, rec.context_window, rec.context_pct) == (412000, 1000000, 0.412)


def test_context_without_tokens_clears_the_pct():
	reg = _reg()
	reg.apply_telemetry("s1", {"context": {"tokens": 1000, "window": 200000}})
	rec = reg.apply_telemetry("s1", {"context": {"tokens": None, "window": 200000}})
	assert (rec.context_tokens, rec.context_window, rec.context_pct) == (None, 200000, None)


def test_model_and_effort_set_and_a_null_effort_clears():
	reg = _reg()
	reg.apply_telemetry("s1", {"model": "claude-opus-5-5", "effort": "xhigh"})
	rec = reg.apply_telemetry("s1", {"model": "claude-haiku-5-5", "effort": None})
	assert (rec.model, rec.effort) == ("claude-haiku-5-5", None)


def test_a_report_without_model_leaves_model_and_effort():
	reg = _reg()
	reg.apply_telemetry("s1", {"model": "claude-opus-5-5", "effort": "high"})
	rec = reg.apply_telemetry("s1", {"context": {"tokens": 1, "window": 10}})
	assert (rec.model, rec.effort) == ("claude-opus-5-5", "high")


def test_title_sets_name_and_source():
	reg = _reg()
	rec = reg.apply_telemetry("s1", {"title": {"name": "Fix undo", "source": "custom-title"}})
	assert (rec.name, rec.name_source) == ("Fix undo", "custom-title")


def test_a_report_for_an_unknown_session_creates_it_and_session_start_keeps_the_fields():
	# session.measure fires about 270 ms before session.start in interactive sessions.
	reg = _reg()
	reg.apply_telemetry("s1", {"context": {"tokens": 10, "window": 100}, "model": "claude-opus-5-5", "effort": "high"})
	assert reg.get("s1").source == "telemetry"
	rec = reg.record_session_start("s1", cwd="C:/Work/X")
	assert (rec.cwd, rec.context_tokens, rec.model, rec.effort) == ("C:/Work/X", 10, "claude-opus-5-5", "high")


def test_an_ended_record_ignores_reports():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.record_session_end("s1", reason="exit", ended_at="2026-10-10T11:00:00+00:00")
	assert reg.apply_telemetry("s1", {"model": "claude-opus-5-5"}) is None
	rec = reg.get("s1")
	assert (rec.model, rec.last_event_at) == (None, "2026-10-10T11:00:00+00:00")


def test_a_report_updates_a_lost_record_without_reviving_it():
	reg = _reg()
	rec = reg.record_session_start("s1", cwd="C:/Work/X")
	rec.state = "lost"
	reg.apply_telemetry("s1", {"model": "claude-opus-5-5"})
	assert (reg.get("s1").state, reg.get("s1").model) == ("lost", "claude-opus-5-5")


def test_an_identical_report_does_not_rewrite_the_mirror():
	calls = []
	reg = _reg()
	reg.set_mirror(lambda sid, payload: calls.append(payload))
	reg.apply_telemetry("s1", {"model": "claude-opus-5-5", "effort": "high"})
	n = len(calls)
	reg.apply_telemetry("s1", {"model": "claude-opus-5-5", "effort": "high"})
	assert len(calls) == n
	assert calls[-1]["model_label"] == "Opus 5.5 · high"


def test_context_fields_survive_hydration():
	reg = _reg()
	reg.apply_telemetry("s1", {"context": {"tokens": 10, "window": 100}})
	reg2 = _reg()
	reg2.hydrate_record(reg.get("s1").to_payload())
	rec = reg2.get("s1")
	assert (rec.context_tokens, rec.context_window, rec.context_pct) == (10, 100, 0.1)


_FP = "32495c7f04ecccc8c3dddc24cf463335502ae7eb098a8cffce64442bb67c06b5"


def test_parse_keeps_a_valid_mod_fingerprint_and_drops_a_bad_one():
	assert parse_telemetry({"mod_fingerprint": _FP}) == {"mod_fingerprint": _FP}
	for bad in (_FP.upper(), _FP[:-1], 42, None, _FP + "0"):
		assert parse_telemetry({"mod_fingerprint": bad}) == {}


_OTHER = "0" * 64


def test_a_reported_fingerprint_is_judged_against_the_repo():
	reg = _reg()
	reg.set_repo_mod_fingerprint(_FP)
	rec = reg.apply_telemetry("S1", {"mod_fingerprint": _FP})
	assert (rec.mod_fingerprint, rec.mod_stale) == (_FP, False)
	rec = reg.apply_telemetry("S2", {"mod_fingerprint": _OTHER})
	assert rec.mod_stale is True
	assert reg.apply_telemetry("S3", {"model": "claude-opus-5-5"}).mod_stale is None


def test_a_null_repo_fingerprint_judges_nothing():
	reg = _reg()
	reg.apply_telemetry("S1", {"mod_fingerprint": _FP})
	assert reg.get("S1").mod_stale is None
	reg.set_repo_mod_fingerprint(None)
	assert reg.get("S1").mod_stale is None
	assert reg.mod_health() == {"repo_fingerprint": None, "stale_sessions": []}


def test_a_repo_change_rejudges_and_mirrors():
	reg = _reg()
	mirrored = []
	reg.set_mirror(lambda sid, payload: mirrored.append((sid, payload and payload["mod_stale"])))
	reg.set_repo_mod_fingerprint(_FP)
	reg.apply_telemetry("S1", {"mod_fingerprint": _FP})
	mirrored.clear()
	reg.set_repo_mod_fingerprint(_OTHER)
	assert reg.get("S1").mod_stale is True
	assert mirrored == [("S1", True)]
	reg.set_repo_mod_fingerprint(_OTHER)
	assert mirrored == [("S1", True)]


def test_a_record_hydrated_before_the_change_has_no_fingerprint():
	reg = _reg()
	reg.set_repo_mod_fingerprint(_FP)
	reg.hydrate_record({"cli_session_id": "S1", "state": "idle", "last_event_at": "2026-10-10T12:00:00+00:00"})
	rec = reg.get("S1")
	assert (rec.mod_fingerprint, rec.mod_stale) == (None, None)
	reg.apply_telemetry("S1", {"mod_fingerprint": _OTHER})
	assert rec.mod_stale is True


def test_mod_health_lists_live_and_lost_stale_sessions_never_ended_ones():
	reg = _reg()
	reg.set_repo_mod_fingerprint(_FP)
	for sid in ("S1", "S2", "S3", "S4"):
		reg.apply_telemetry(sid, {"mod_fingerprint": _OTHER})
	reg.apply_telemetry("S4", {"mod_fingerprint": _FP})
	reg.get("S2").state = "lost"
	reg.record_session_end("S3", reason="exit", ended_at="2026-10-10T12:00:00+00:00")
	assert reg.mod_health() == {"repo_fingerprint": _FP, "stale_sessions": ["S1", "S2"]}
