"""Session liveness from the mod's inbox poll: note_poll, the sweep's rules,
and the grace period after a start or a sleep."""

import asyncio
import datetime as _dt

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from server.registry import Registry
from server.session_registry import LivenessGrace, SessionRegistry

T0 = "2026-10-10T12:00:00+00:00"
LAST = _dt.datetime.fromisoformat(T0).timestamp()


class _Mono:
	def __init__(self) -> None:
		self.t = 1000.0

	def __call__(self) -> float:
		return self.t


def _reg(mono=None):
	return SessionRegistry(now=lambda: T0, mono=mono)


def _sweep(reg, now_ts=LAST + 100, **overrides):
	args = dict(now_ts=now_ts, lost_after_seconds=900, poll_lost_after_seconds=90, retention_seconds=72 * 3600)
	args.update(overrides)
	return reg.sweep(**args)


def test_a_poll_from_a_known_session_writes_nothing_to_firebase():
	calls = []
	reg = _reg()
	reg.set_mirror(lambda sid, payload: calls.append(sid))
	reg.record_session_start("s1", cwd="C:/Work/X")
	n = len(calls)
	reg.note_poll("s1")
	assert len(calls) == n


def test_a_poll_from_an_unknown_session_creates_its_record():
	calls = []
	reg = _reg()
	reg.set_mirror(lambda sid, payload: calls.append(sid))
	reg.note_poll("s-new")
	assert reg.get("s-new").source == "poll"
	assert calls == ["s-new"]


def test_a_poll_revives_a_lost_session():
	reg = _reg()
	rec = reg.record_session_start("s1", cwd="C:/Work/X")
	rec.state, rec.end_reason = "lost", "presumed-dead"
	reg.note_poll("s1")
	rec = reg.get("s1")
	assert (rec.state, rec.end_reason, rec.last_transition_source) == ("idle", None, "poll")


def test_a_poll_never_revives_an_ended_session():
	reg = _reg()
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.record_session_end("s1", reason="exit", ended_at=T0)
	reg.note_poll("s1")
	assert reg.get("s1").state == "ended"


def test_a_polled_session_is_lost_only_after_the_poll_threshold():
	mono = _Mono()
	reg = _reg(mono)
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.note_poll("s1")
	mono.t += 89
	_sweep(reg, now_ts=LAST + 10_000)  # silent for hours, but polling
	assert reg.get("s1").state == "idle"
	mono.t += 2
	_sweep(reg, now_ts=LAST + 10_000)
	rec = reg.get("s1")
	assert (rec.state, rec.end_reason) == ("lost", "presumed-dead")


def test_a_never_polled_session_keeps_the_silence_rule():
	reg = _reg()
	reg.record_session_start("s-p", cwd="C:/Work/X")
	_sweep(reg, now_ts=LAST + 800)
	assert reg.get("s-p").state == "idle"
	_sweep(reg, now_ts=LAST + 1000)
	assert reg.get("s-p").state == "lost"


def test_the_grace_period_marks_nothing_lost_but_still_prunes():
	mono = _Mono()
	reg = _reg(mono)
	reg.record_session_start("s-polled", cwd="C:/Work/X")
	reg.note_poll("s-polled")
	reg.record_session_start("s-silent", cwd="C:/Work/X")
	reg.record_session_start("s-old", cwd="C:/Work/X")
	reg.record_session_end("s-old", reason="exit", ended_at=T0)
	mono.t += 10_000
	pruned = _sweep(reg, now_ts=LAST + 73 * 3600, in_grace=True)
	assert (reg.get("s-polled").state, reg.get("s-silent").state) == ("idle", "idle")
	assert pruned == ["s-old"]


def test_a_live_ask_does_not_exempt_a_session_whose_polls_stopped():
	# The mod's poll runs through a blocked tool call, so a silent poll means
	# the session is gone even while it holds a live ask.
	mono = _Mono()
	reg = _reg(mono)
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.upsert_from_hook("s1", state="awaiting_human", event="PreToolUse")
	reg.note_poll("s1")
	mono.t += 91
	_sweep(reg, live_ask_ids={"s1"}, live_wait_ids=set())
	assert reg.get("s1").state == "lost"


def test_a_live_ask_still_exempts_a_session_that_never_polled():
	reg = _reg(_Mono())
	reg.record_session_start("s-p", cwd="C:/Work/X")
	reg.upsert_from_hook("s-p", state="awaiting_human", event="PreToolUse")
	_sweep(reg, now_ts=LAST + 10_000, live_ask_ids={"s-p"}, live_wait_ids=set())
	assert reg.get("s-p").state == "awaiting_human"


def test_has_polled_follows_the_poll_stamp():
	reg = _reg(_Mono())
	reg.record_session_start("s1", cwd="C:/Work/X")
	assert reg.has_polled("s1") is False
	reg.note_poll("s1")
	assert reg.has_polled("s1") is True
	reg.record_session_start("s1", cwd="C:/Work/X")  # a resumed life starts unpolled
	assert reg.has_polled("s1") is False


def test_grace_holds_from_start_for_the_grace_seconds():
	grace = LivenessGrace(grace_seconds=90, interval=60, start_ts=1000.0)
	assert grace.tick(1000.0) is True
	assert grace.tick(1060.0) is True
	assert grace.tick(1091.0) is False


def test_a_gap_of_more_than_three_intervals_restarts_grace():
	grace = LivenessGrace(grace_seconds=90, interval=60, start_ts=1000.0)
	grace.tick(1060.0)
	assert grace.tick(1120.0) is False
	assert grace.tick(1301.0) is True   # 181 s since the last tick: the machine slept
	assert grace.tick(1361.0) is True
	assert grace.tick(1392.0) is False


def test_a_short_sleep_past_the_poll_threshold_restarts_grace():
	# A two-minute lid close: every poll stamp is over 90 s old on wake through
	# the sleep alone, so the first tick must not judge them.
	grace = LivenessGrace(grace_seconds=90, interval=60, start_ts=1000.0)
	grace.tick(1060.0)
	assert grace.tick(1120.0) is False
	assert grace.tick(1222.0) is True   # 102 s since the last tick


def test_a_tick_up_to_a_quarter_interval_late_is_not_a_sleep():
	grace = LivenessGrace(grace_seconds=90, interval=60, start_ts=1000.0)
	grace.tick(1060.0)
	assert grace.tick(1120.0) is False
	assert grace.tick(1195.0) is False  # 75 s: a slow tick, not a sleep


def test_a_resumed_session_is_not_judged_by_its_last_lifes_poll():
	# claude --resume keeps the session id; until its first poll the new life
	# must fall under the silence rule, not an hour-old stamp.
	mono = _Mono()
	reg = _reg(mono)
	reg.record_session_start("s1", cwd="C:/Work/X")
	reg.note_poll("s1")
	reg.record_session_end("s1", reason="exit", ended_at=T0)
	mono.t += 3600
	reg.record_session_start("s1", cwd="C:/Work/X")
	_sweep(reg)
	assert reg.get("s1").state == "idle"


def test_the_inbox_route_counts_as_a_poll():
	from server.main import _build_inbox_route
	mono = _Mono()
	reg = _reg(mono)
	reg.record_session_start("s1", cwd="C:/Work/X")
	app = Starlette()
	app.add_route("/sessions/{sid}/inbox", _build_inbox_route(Registry(), reg), methods=["GET"])
	with TestClient(app) as client:
		client.get("/sessions/s1/inbox")
		mono.t += 91
		_sweep(reg)
		assert reg.get("s1").state == "lost"
		client.get("/sessions/s1/inbox")
	assert reg.get("s1").state == "idle"


@pytest.mark.asyncio
async def test_the_first_sweep_tick_after_start_marks_nothing_lost(tmp_path):
	from server.gateway.dispatch import dispatch_session_sweep
	from server.logging_jsonl import JsonlLogger
	from tests.conftest import _make_loop_supervisor

	reg = SessionRegistry()
	reg.record_session_start("s-silent", cwd="C:/Work/X")
	past = _dt.datetime.now(_dt.timezone.utc).timestamp() - 901
	reg.get("s-silent").last_event_at = _dt.datetime.fromtimestamp(past, tz=_dt.timezone.utc).isoformat()
	logger = JsonlLogger(str(tmp_path / "log.jsonl"))
	task = asyncio.create_task(dispatch_session_sweep(
		reg, logger, _make_loop_supervisor(None, logger, "dispatch_session_sweep"),
		lost_after_seconds=900, poll_lost_after_seconds=90, retention_hours=72, interval=10.0,
	))
	for _ in range(5):
		await asyncio.sleep(0)
	assert reg.get("s-silent").state == "idle"
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task
