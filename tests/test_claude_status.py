"""Tests for the ported Claude status parser + watch state machine (from T-179)."""

from datetime import datetime, timedelta, timezone

from server.claude_status import ClaudeStatus, parse_status, ClaudeStatusWatch

T0 = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)


def _summary(indicator, description="", incidents=None):
	import json
	body = {"status": {"indicator": indicator, "description": description}}
	if incidents is not None:
		body["incidents"] = incidents
	return json.dumps(body)


def test_parse_maps_each_indicator():
	assert parse_status(_summary("none", "All Systems Operational"), T0).level == "operational"
	assert parse_status(_summary("minor"), T0).level == "minor"
	assert parse_status(_summary("major"), T0).level == "major"
	assert parse_status(_summary("critical"), T0).level == "critical"
	# Present-but-unrecognized indicator -> unknown (not None).
	assert parse_status(_summary("weird"), T0).level == "unknown"


def test_parse_extracts_only_unresolved_incidents():
	incidents = [
		{"name": "Active outage", "status": "investigating"},
		{"name": "Old one", "status": "resolved"},
		{"name": "Done", "status": "postmortem"},
	]
	s = parse_status(_summary("major", "Partial outage", incidents), T0)
	assert s.incidents == ["Active outage"]


def test_parse_incident_status_filter_is_case_insensitive():
	# status.claude.com emits lowercase, but the C# source this ports filters with
	# OrdinalIgnoreCase; a mixed-case closed status must still be treated as closed.
	incidents = [{"name": "Should be filtered", "status": "Resolved"}]
	s = parse_status(_summary("major", "x", incidents), T0)
	assert s.incidents == []


def test_parse_returns_none_on_malformed_or_missing_indicator():
	assert parse_status("not json", T0) is None
	assert parse_status('{"status": {}}', T0) is None
	assert parse_status('{"nope": 1}', T0) is None


def test_parse_stamps_fetched_at_and_description():
	s = parse_status(_summary("none", "All Systems Operational"), T0)
	assert s.fetched_at == T0
	assert s.description == "All Systems Operational"


def _watch():
	return ClaudeStatusWatch(max_watch_minutes=180)


def test_idle_degraded_starts_watching():
	w = _watch()
	action = w.apply_fetch(ClaudeStatus("minor", "Degraded", [], T0), T0)
	assert action == "start_polling"
	assert w.state == "watching"


def test_idle_operational_stays_idle():
	w = _watch()
	action = w.apply_fetch(ClaudeStatus("operational", "All good", [], T0), T0)
	assert action == "none"
	assert w.state == "idle"


def test_watching_operational_resolves():
	w = _watch()
	w.apply_fetch(ClaudeStatus("major", "Outage", [], T0), T0)
	action = w.apply_fetch(ClaudeStatus("operational", "Recovered", [], T0), T0)
	assert action == "stop_polling"
	assert w.state == "resolved_unacked"


def test_watching_unknown_does_not_stop():
	w = _watch()
	w.apply_fetch(ClaudeStatus("major", "Outage", [], T0), T0)
	action = w.apply_fetch(ClaudeStatus("unknown", "Unreachable", [], T0), T0)
	assert action == "none"
	assert w.state == "watching"


def test_watching_caps_after_max_minutes():
	w = _watch()
	w.apply_fetch(ClaudeStatus("major", "Outage", [], T0), T0)
	later = T0 + timedelta(minutes=180)
	action = w.apply_fetch(ClaudeStatus("major", "Still down", [], later), later)
	assert action == "stop_polling"
	assert w.state == "capped_unacked"


def test_acknowledge_from_each_nonidle_returns_idle():
	for setup_level, after in (("major", "watching"),):
		w = _watch()
		w.apply_fetch(ClaudeStatus(setup_level, "x", [], T0), T0)
		assert w.state == after
		assert w.acknowledge() == "stop_polling"
		assert w.state == "idle"
	# acknowledge from idle is a no-op
	w2 = _watch()
	assert w2.acknowledge() == "none"


def test_snapshot_shape_and_button_labels():
	w = _watch()
	# idle -> button check, dot hidden
	snap = w.snapshot()
	assert snap["watch_state"] == "idle" and snap["button"] == "check" and snap["dot_visible"] is False
	# watching -> button stop, dot visible at the degraded level
	w.apply_fetch(ClaudeStatus("major", "Partial outage", ["X"], T0), T0)
	snap = w.snapshot()
	assert snap["watch_state"] == "watching" and snap["button"] == "stop"
	assert snap["dot_visible"] is True and snap["level"] == "major"
	assert snap["incidents"] == ["X"] and snap["description"] == "Partial outage"
	# resolved -> button clear, level operational
	w.apply_fetch(ClaudeStatus("operational", "Recovered", [], T0), T0)
	snap = w.snapshot()
	assert snap["watch_state"] == "resolved_unacked" and snap["button"] == "clear" and snap["level"] == "operational"
