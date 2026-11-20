"""The pre-deploy dry run reports what the first run will repair and end."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lifecycle_dry_run.py"
_spec = importlib.util.spec_from_file_location("lifecycle_dry_run", _PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
plan_first_run = _module.plan_first_run

DAY = 86400.0
NOW = 1_800_000_000.0


def _conv(title, *, alive, sid, last_days, hidden=False, ended_iso=None):
	return {
		"meta": {"title": title, "state": "active", "hidden": hidden, "created_at": NOW - 60 * DAY, "last_activity_at": NOW - last_days * DAY},
		"members_active": {"Claude": {
			"cli_session_id": sid, "sender": "Claude", "cwd": "", "surface": "windows", "joined_at": 1.0,
			"alive": alive, "session_ended_at": ended_iso,
		}},
	}


def _conversations():
	return {
		"conv-home": _conv("(home)", alive=True, sid="gone", last_days=37, hidden=True),
		"conv-old": _conv("old", alive=False, sid="ended", last_days=20),
		"conv-young": _conv("young", alive=False, sid="ended2", last_days=5),
		"conv-live": _conv("live", alive=True, sid="live", last_days=30),
	}


def test_the_plan_matches_what_the_server_will_do():
	sessions = {"live": {"cli_session_id": "live", "state": "idle", "last_event_at": "2026-11-19T00:00:00+00:00"}}
	plan = plan_first_run(_conversations(), sessions, idle_days=14, now_ts=NOW)
	assert plan["repaired"] == [("conv-home", "(home)", "Claude")]
	assert sorted(row[0] for row in plan["ended"]) == ["conv-home", "conv-old"]
	assert sorted(row[0] for row in plan["kept"]) == ["conv-live", "conv-young"]


def test_an_empty_sessions_node_repairs_nothing_and_says_so():
	plan = plan_first_run(_conversations(), {}, idle_days=14, now_ts=NOW)
	assert plan["repaired"] == []
	assert plan["repair_skipped"] is True
	assert [row[0] for row in plan["ended"]] == ["conv-old"]
	assert [row[0] for row in plan["kept"]] == ["conv-home", "conv-live", "conv-young"]


def test_kept_idle_days_count_a_members_session_end_and_alive_flag():
	import datetime
	ended = datetime.datetime.fromtimestamp(NOW - 2 * DAY, datetime.timezone.utc).isoformat()
	conversations = {"conv-a": _conv("a", alive=False, sid="s1", last_days=40, ended_iso=ended)}
	plan = plan_first_run(conversations, {"x": {"cli_session_id": "x", "state": "idle"}}, idle_days=14, now_ts=NOW)
	assert plan["ended"] == []
	(row,) = plan["kept"]
	assert row[0] == "conv-a"
	assert 1.9 < row[2] < 2.1
	assert row[3] is False


def test_rows_carry_the_open_question_record_count():
	conversations = _conversations()
	conversations["conv-old"]["pending_questions"] = {"r1": {"sender": "Claude"}, "r2": {"sender": "Claude"}}
	conversations["conv-live"]["pending_questions"] = {"r3": {}}
	sessions = {"live": {"cli_session_id": "live", "state": "idle", "last_event_at": "2026-11-19T00:00:00+00:00"}}
	plan = plan_first_run(conversations, sessions, idle_days=14, now_ts=NOW)
	ended = {row[0]: row for row in plan["ended"]}
	kept = {row[0]: row for row in plan["kept"]}
	assert ended["conv-old"][4] == 2
	assert ended["conv-home"][4] == 0
	assert kept["conv-live"][4] == 1
	assert kept["conv-live"][3] is True


def test_format_plan_prints_counts_rows_and_notes():
	conversations = _conversations()
	conversations["conv-old"]["pending_questions"] = {"r1": {}, "r2": {}}
	conversations["conv-nots"] = {"meta": {"title": "nots", "state": "active"}, "members_active": {}}
	sessions = {"live": {"cli_session_id": "live", "state": "idle", "last_event_at": "2026-11-19T00:00:00+00:00"}}
	plan = plan_first_run(conversations, sessions, idle_days=14, now_ts=NOW)
	lines = _module.format_plan(plan, 14)
	text = "\n".join(lines)
	assert "Members repaired (made dormant): 1" in text
	assert "Conversations ended (idle 14 days): 2" in text
	assert "Conversations kept: 3" in text
	assert "NOTE: conv-old has 2 open question record(s); the server may keep it" in text
	assert "conv-home has" not in text
	assert "no usable timestamp" in text
	assert "alive_member=yes" in text
	assert "alive_member=no" in text


def test_format_plan_notes_a_skipped_repair():
	plan = plan_first_run(_conversations(), {}, idle_days=14, now_ts=NOW)
	lines = _module.format_plan(plan, 14)
	assert any("skip the member repair" in line for line in lines)
