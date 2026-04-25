"""Tests for the pending-request registry."""

import asyncio
import json
from datetime import datetime

import pytest

from server.registry import PendingRequest, Registry


class TestPendingByKey:
	def test_add_creates_entry(self):
		async def run():
			r = Registry()
			fut = r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			assert isinstance(fut, asyncio.Future)
			assert r.get(("c:/work/sw", "Claude")) is not None
			assert r.get(("c:/work/sw", "Claude")).request_id == "r1"
		asyncio.run(run())

	def test_supersede_cancels_old_future(self):
		async def run():
			r = Registry()
			old_fut = r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r2")
			assert old_fut.cancelled()
			assert r.get(("c:/work/sw", "Claude")).request_id == "r2"
		asyncio.run(run())

	def test_supersede_returns_prior_request_id(self):
		async def run():
			r = Registry()
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			fut, prior = r.add(cwd="c:/work/sw", sender="Claude", request_id="r2",
			                   return_superseded=True)
			assert prior == "r1"
			assert isinstance(fut, asyncio.Future)
		asyncio.run(run())

	def test_supersede_returns_none_when_slot_was_empty(self):
		async def run():
			r = Registry()
			fut, prior = r.add(cwd="c:/work/sw", sender="Claude", request_id="r1",
			                   return_superseded=True)
			assert prior is None
		asyncio.run(run())

	def test_supersede_is_per_sender(self):
		async def run():
			r = Registry()
			fut_a = r.add(cwd="c:/work/sw", sender="Alice", request_id="r1")
			fut_b = r.add(cwd="c:/work/sw", sender="Bob", request_id="r2")
			assert not fut_a.cancelled()
			assert not fut_b.cancelled()
		asyncio.run(run())

	def test_resolve_by_key(self):
		async def run():
			r = Registry()
			fut = r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			req_id = r.resolve(cwd="c:/work/sw", sender="Claude", text="answer")
			assert req_id == "r1"
			assert fut.result() == "answer"
			assert r.get(("c:/work/sw", "Claude")) is None
		asyncio.run(run())

	def test_resolve_unknown_returns_none(self):
		r = Registry()
		req_id = r.resolve(cwd="c:/work/sw", sender="Claude", text="orphan")
		assert req_id is None

	def test_remove_by_key(self):
		async def run():
			r = Registry()
			fut = r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			req_id = r.remove(cwd="c:/work/sw", sender="Claude")
			assert req_id == "r1"
			assert fut.cancelled()
			assert r.get(("c:/work/sw", "Claude")) is None
		asyncio.run(run())

	def test_remove_unknown_returns_none(self):
		r = Registry()
		req_id = r.remove(cwd="c:/work/sw", sender="Claude")
		assert req_id is None

	def test_all_pending_snapshot(self):
		async def run():
			r = Registry()
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			r.add(cwd="c:/work/rpdm", sender="Claude", request_id="r2")
			r.add(cwd="c:/work/sw", sender="Bob", request_id="r3")
			pending = r.all_pending()
			assert len(pending) == 3
			req_ids = sorted(p.request_id for p in pending)
			assert req_ids == ["r1", "r2", "r3"]
		asyncio.run(run())


def test_away_mode_defaults_false_when_no_path():
	registry = Registry()
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_away_mode_defaults_false_when_file_missing(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away-mode.json")
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_away_mode_set_true_persists(tmp_path):
	path = tmp_path / "away-mode.json"
	registry = Registry(away_mode_path=path)
	registry.set_global_away(True)
	data = json.loads(path.read_text(encoding="utf-8"))
	assert data["global"] is True
	assert "overrides" in data


def test_away_mode_set_false_persists(tmp_path):
	path = tmp_path / "away-mode.json"
	registry = Registry(away_mode_path=path)
	registry.set_global_away(True)
	registry.set_global_away(False)
	data = json.loads(path.read_text(encoding="utf-8"))
	assert data["global"] is False


def test_away_mode_round_trip_across_registry_instances(tmp_path):
	path = tmp_path / "away-mode.json"
	r1 = Registry(away_mode_path=path)
	r1.set_global_away(True)
	r2 = Registry(away_mode_path=path)
	assert r2.is_away_mode_active("c:/work/switchboard") is True


def test_away_mode_corrupt_file_defaults_false(tmp_path):
	path = tmp_path / "away-mode.json"
	path.write_text("not json at all {{{", encoding="utf-8")
	registry = Registry(away_mode_path=path)
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_away_mode_set_is_idempotent(tmp_path):
	path = tmp_path / "away-mode.json"
	registry = Registry(away_mode_path=path)
	registry.set_global_away(True)
	registry.set_global_away(True)
	assert registry.is_away_mode_active("c:/work/switchboard") is True
	registry.set_global_away(False)
	registry.set_global_away(False)
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_away_mode_no_path_set_does_not_crash():
	registry = Registry()  # no path
	registry.set_global_away(True)
	assert registry.is_away_mode_active("c:/work/switchboard") is True
	registry.set_global_away(False)
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_set_away_mode_invokes_callback(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away.json")
	calls: list = []
	registry.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
	registry.set_global_away(True)
	registry.set_global_away(False)
	assert calls == [(None, True), (None, False)]


def test_set_away_mode_without_callback_is_noop(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away.json")
	# No callback registered; must not raise.
	registry.set_global_away(True)
	assert registry.is_away_mode_active("c:/work/switchboard") is True


def test_set_away_mode_callback_is_called_after_persist(tmp_path):
	registry = Registry(away_mode_path=tmp_path / "away.json")
	seen_from_disk: list[bool] = []

	def _cb(cwd, active: bool) -> None:
		# By the time the callback fires, the sidecar must already reflect
		# the new value, so a fresh Registry loading the same path sees it.
		other = Registry(away_mode_path=tmp_path / "away.json")
		seen_from_disk.append(other.is_away_mode_active("c:/anywhere"))

	registry.set_away_mode_callback(_cb)
	registry.set_global_away(True)
	assert seen_from_disk == [True]


def test_set_away_mode_callback_failure_does_not_propagate(tmp_path, caplog):
	registry = Registry(away_mode_path=tmp_path / "away.json")

	def _cb(cwd, active: bool) -> None:
		raise RuntimeError("boom")

	registry.set_away_mode_callback(_cb)
	# Must not raise.
	import logging
	with caplog.at_level(logging.ERROR):
		registry.set_global_away(True)
	assert registry.is_away_mode_active("c:/work/switchboard") is True
	assert any("away_mode_callback" in rec.message for rec in caplog.records)


class TestTwoTierAwayMode:
	def test_default_is_inactive(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		assert r.is_away_mode_active("c:/work/switchboard") is False

	def test_global_on_no_overrides(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_global_away(True)
		assert r.is_away_mode_active("c:/work/switchboard") is True
		assert r.is_away_mode_active("c:/work/rpdm") is True

	def test_per_cwd_override_when_global_off(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_cwd_override("c:/work/switchboard", True)
		assert r.is_away_mode_active("c:/work/switchboard") is True
		assert r.is_away_mode_active("c:/work/rpdm") is False

	def test_per_cwd_exempt_when_global_on(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_global_away(True)
		r.set_cwd_override("c:/work/switchboard", False)
		assert r.is_away_mode_active("c:/work/switchboard") is False
		assert r.is_away_mode_active("c:/work/rpdm") is True

	def test_set_global_clears_overrides(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_cwd_override("c:/work/switchboard", True)
		r.set_global_away(True)
		assert r.cwd_overrides() == {}

	def test_set_global_off_clears_overrides(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_global_away(True)
		r.set_cwd_override("c:/work/switchboard", False)
		r.set_global_away(False)
		assert r.cwd_overrides() == {}

	def test_remove_cwd_override(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_cwd_override("c:/work/switchboard", True)
		r.remove_cwd_override("c:/work/switchboard")
		assert "c:/work/switchboard" not in r.cwd_overrides()

	def test_callback_fires_on_global_change(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		calls = []
		r.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
		r.set_global_away(True)
		assert calls == [(None, True)]

	def test_callback_fires_on_cwd_change(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		calls = []
		r.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
		r.set_cwd_override("c:/work/switchboard", True)
		assert calls == [("c:/work/switchboard", True)]

	def test_callback_no_fire_on_redundant_global_set(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_global_away(True)
		calls = []
		r.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
		r.set_global_away(True)  # already True, no overrides
		assert calls == []

	def test_callback_no_fire_on_redundant_cwd_set(self, tmp_path):
		r = Registry(away_mode_path=tmp_path / "away.json")
		r.set_cwd_override("c:/work/switchboard", True)
		calls = []
		r.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
		# Resolution unchanged: still True
		r.set_cwd_override("c:/work/switchboard", True)
		assert calls == []


class TestSidecarPersistence:
	def test_persist_global_only(self, tmp_path):
		path = tmp_path / "away.json"
		r = Registry(away_mode_path=path)
		r.set_global_away(True)
		r2 = Registry(away_mode_path=path)
		assert r2.is_away_mode_active("c:/anywhere") is True

	def test_persist_overrides(self, tmp_path):
		path = tmp_path / "away.json"
		r = Registry(away_mode_path=path)
		r.set_global_away(True)
		r.set_cwd_override("c:/work/switchboard", False)
		r2 = Registry(away_mode_path=path)
		assert r2.is_away_mode_active("c:/work/switchboard") is False
		assert r2.is_away_mode_active("c:/work/rpdm") is True

	def test_load_legacy_v1_shape_silently_discarded(self, tmp_path):
		# V1 sidecar shape: {"active": bool, "entered_at": ISO}
		path = tmp_path / "away.json"
		path.write_text('{"active": true, "entered_at": "2026-04-23T10:00:00+00:00"}')
		r = Registry(away_mode_path=path)
		# Legacy file is silently discarded; new state starts clean
		assert r.is_away_mode_active("c:/anywhere") is False

	def test_first_write_replaces_legacy_shape(self, tmp_path):
		path = tmp_path / "away.json"
		path.write_text('{"active": true, "entered_at": "2026-04-23T10:00:00+00:00"}')
		r = Registry(away_mode_path=path)
		r.set_global_away(True)
		import json
		data = json.loads(path.read_text())
		assert "global" in data
		assert "active" not in data
