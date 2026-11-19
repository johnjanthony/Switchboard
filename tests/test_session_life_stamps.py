"""SessionRegistry's in-memory stamps: latest sign of life and time bound."""
from server.session_registry import SessionRegistry


def _reg():
	clock = {"t": 100.0}
	reg = SessionRegistry(now=lambda: "2026-11-19T12:00:00+00:00", mono=lambda: clock["t"])
	return reg, clock


def test_each_sign_of_life_stamps():
	calls = [
		lambda r: r.note_poll("s"),
		lambda r: r.upsert_from_hook("s", state="active", event="PreToolUse"),
		lambda r: r.record_session_start("s", cwd="C:/X"),
		lambda r: r.touch_mcp("s", cwd="C:/X"),
		lambda r: r.apply_telemetry("s", {}),
		lambda r: r.set_awaiting_approval("s", "2026-11-19T12:00:00+00:00"),
	]
	for call in calls:
		reg, clock = _reg()
		assert reg.last_life("s") is None
		clock["t"] = 250.0
		call(reg)
		assert reg.last_life("s") == 250.0


def test_clearing_an_approval_is_not_a_sign_of_life():
	reg, clock = _reg()
	reg.set_awaiting_approval("s", "2026-11-19T12:00:00+00:00")
	clock["t"] = 400.0
	reg.set_awaiting_approval("s", None)
	assert reg.last_life("s") == 100.0


def test_an_ended_record_still_stamps_a_poll():
	reg, clock = _reg()
	reg.record_session_start("s", cwd="C:/X")
	reg.record_session_end("s", reason="logout", ended_at="2026-11-19T12:00:00+00:00")
	clock["t"] = 300.0
	reg.note_poll("s")
	assert reg.get("s").state == "ended"
	assert reg.last_life("s") == 300.0


def test_binding_stamps_without_a_record_and_unbinding_clears():
	reg, clock = _reg()
	reg.set_binding("nobody", "conv-1")
	assert reg.bound_at("nobody") == 100.0
	clock["t"] = 180.0
	reg.set_binding("nobody", "conv-2")
	assert reg.bound_at("nobody") == 180.0
	reg.set_binding("nobody", None)
	assert reg.bound_at("nobody") is None


def test_mono_is_the_injected_clock():
	reg, clock = _reg()
	clock["t"] = 7.5
	assert reg.mono() == 7.5


def test_pruning_a_record_drops_its_stamps():
	reg, clock = _reg()
	reg.record_session_start("s", cwd="C:/X")
	reg.set_binding("s", "conv-1")
	reg.record_session_end("s", reason="logout", ended_at="2026-11-01T00:00:00+00:00")
	from datetime import datetime
	now_ts = datetime.fromisoformat("2026-11-19T12:00:00+00:00").timestamp()
	pruned = reg.sweep(now_ts=now_ts, lost_after_seconds=900, poll_lost_after_seconds=90, retention_seconds=72 * 3600)
	assert pruned == ["s"]
	assert reg.last_life("s") is None and reg.bound_at("s") is None
