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

	def test_cancel_pending_for_cwd_cancels_matching_only(self):
		"""Cancel-on-spawn: cancel + pop every pending whose cwd matches; leave siblings on
		other cwds untouched. Returns the cancelled request_ids in registry-iteration order."""
		async def run():
			r = Registry()
			fut1 = r.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")
			fut2 = r.add(cwd="c:/work/foo", sender="Sparkles", request_id="req-2")
			fut3 = r.add(cwd="c:/work/bar", sender="Claude", request_id="req-3")

			cancelled = r.cancel_pending_for_cwd("c:/work/foo")

			assert sorted(cancelled) == ["req-1", "req-2"]
			assert fut1.cancelled() and fut2.cancelled()
			assert not fut3.cancelled()
			assert r.get(("c:/work/foo", "Claude")) is None
			assert r.get(("c:/work/foo", "Sparkles")) is None
			assert r.get(("c:/work/bar", "Claude")) is not None
		asyncio.run(run())

	def test_cancel_pending_for_cwd_no_match_returns_empty(self):
		async def run():
			r = Registry()
			r.add(cwd="c:/work/foo", sender="Claude", request_id="req-1")
			cancelled = r.cancel_pending_for_cwd("c:/work/missing")
			assert cancelled == []
			# Existing entry untouched
			assert r.get(("c:/work/foo", "Claude")) is not None
		asyncio.run(run())


def test_away_mode_defaults_false_when_no_path():
	registry = Registry()
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_away_mode_update_cache_is_idempotent():
	registry = Registry()
	registry.update_global_away_cache(True)
	registry.update_global_away_cache(True)
	assert registry.is_away_mode_active("c:/work/switchboard") is True
	registry.update_global_away_cache(False)
	registry.update_global_away_cache(False)
	assert registry.is_away_mode_active("c:/work/switchboard") is False


def test_cwd_override_inherited_by_subdirectory():
	"""A descendant cwd inherits the nearest ancestor's override."""
	registry = Registry()
	registry.update_global_away_cache(True)
	# Project root has explicit at-desk override
	registry.set_cwd_override("c:/work/switchboard", False)
	registry.update_cwd_override_cache("c:/work/switchboard", False)
	# Subdirectory inherits the override even though it has no entry of its own
	assert registry.is_away_mode_active("c:/work/switchboard/android") is False
	assert registry.is_away_mode_active("c:/work/switchboard/server/firebase.py") is False
	# Unrelated path falls through to global
	assert registry.is_away_mode_active("c:/work/other") is True
	# Direct subdir override wins over ancestor
	registry.set_cwd_override("c:/work/switchboard/android", True)
	registry.update_cwd_override_cache("c:/work/switchboard/android", True)
	assert registry.is_away_mode_active("c:/work/switchboard/android") is True
	assert registry.is_away_mode_active("c:/work/switchboard/android/app") is True
	assert registry.is_away_mode_active("c:/work/switchboard/server") is False  # still inherits root


def test_set_cwd_override_invokes_callback():
	registry = Registry()
	calls: list = []
	registry.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
	registry.set_cwd_override("c:/work/sw", True)
	assert calls == [("c:/work/sw", True)]


def test_set_cwd_override_without_callback_is_noop():
	registry = Registry()
	# No callback registered; must not raise.
	registry.set_cwd_override("c:/work/sw", True)
	registry.update_cwd_override_cache("c:/work/sw", True)
	assert registry.is_away_mode_active("c:/work/sw") is True


def test_set_cwd_override_callback_failure_does_not_propagate(caplog):
	registry = Registry()

	def _cb(cwd, active: bool) -> None:
		raise RuntimeError("boom")

	registry.set_away_mode_callback(_cb)
	# Must not raise.
	import logging
	with caplog.at_level(logging.ERROR):
		registry.set_cwd_override("c:/work/sw", True)
	# Mimic listener fire
	registry.update_cwd_override_cache("c:/work/sw", True)
	assert registry.is_away_mode_active("c:/work/sw") is True
	assert any("away_mode_callback" in rec.message for rec in caplog.records)


class TestTwoTierAwayMode:
	def test_default_is_inactive(self):
		r = Registry()
		assert r.is_away_mode_active("c:/work/switchboard") is False

	def test_global_on_no_overrides(self):
		r = Registry()
		r.update_global_away_cache(True)
		assert r.is_away_mode_active("c:/work/switchboard") is True
		assert r.is_away_mode_active("c:/work/rpdm") is True

	def test_per_cwd_override_when_global_off(self):
		r = Registry()
		r.update_cwd_override_cache("c:/work/switchboard", True)
		assert r.is_away_mode_active("c:/work/switchboard") is True
		assert r.is_away_mode_active("c:/work/rpdm") is False

	def test_per_cwd_exempt_when_global_on(self):
		r = Registry()
		r.update_global_away_cache(True)
		r.update_cwd_override_cache("c:/work/switchboard", False)
		assert r.is_away_mode_active("c:/work/switchboard") is False
		assert r.is_away_mode_active("c:/work/rpdm") is True

	def test_remove_cwd_override(self):
		r = Registry()
		r.update_cwd_override_cache("c:/work/switchboard", True)
		r.remove_cwd_override("c:/work/switchboard")
		r.update_cwd_override_cache("c:/work/switchboard", None)
		assert "c:/work/switchboard" not in r.cwd_overrides()

	def test_callback_fires_on_cwd_change(self):
		r = Registry()
		calls = []
		r.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
		r.set_cwd_override("c:/work/switchboard", True)
		assert calls == [("c:/work/switchboard", True)]

	def test_callback_no_fire_on_redundant_cwd_set(self):
		# After Task 6, set_cwd_override is unconditional — it always fires the
		# callback. Idempotency moves to the listener / Firebase layer (the
		# listener no-ops on identical writes). This test now verifies that
		# behavior: same value => still fires.
		r = Registry()
		r.update_cwd_override_cache("c:/work/switchboard", True)
		calls = []
		r.set_away_mode_callback(lambda cwd, active: calls.append((cwd, active)))
		# Unconditional write — callback fires every time.
		r.set_cwd_override("c:/work/switchboard", True)
		assert calls == [("c:/work/switchboard", True)]


class TestPendingMirror:
	def test_add_calls_mirror_with_plus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda cwd, delta: calls.append((cwd, delta)))
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			assert calls == [("c:/work/sw", 1)]
		asyncio.run(run())

	def test_resolve_calls_mirror_with_minus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda cwd, delta: calls.append((cwd, delta)))
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			calls.clear()
			r.resolve("c:/work/sw", "Claude", "ok")
			assert calls == [("c:/work/sw", -1)]
		asyncio.run(run())

	def test_resolve_missing_does_not_call_mirror(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda cwd, delta: calls.append((cwd, delta)))
			r.resolve("c:/work/sw", "Claude", "ok")
			assert calls == []
		asyncio.run(run())

	def test_remove_calls_mirror_with_minus_one(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda cwd, delta: calls.append((cwd, delta)))
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			calls.clear()
			r.remove("c:/work/sw", "Claude")
			assert calls == [("c:/work/sw", -1)]
		asyncio.run(run())

	def test_supersede_via_add_emits_minus_one_then_plus_one(self):
		"""When add() supersedes an existing entry, the prior is cancelled (mirror -1)
		and the new is added (mirror +1). Two calls."""
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda cwd, delta: calls.append((cwd, delta)))
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r1")
			calls.clear()
			r.add(cwd="c:/work/sw", sender="Claude", request_id="r2")
			assert calls == [("c:/work/sw", -1), ("c:/work/sw", 1)]
		asyncio.run(run())

	def test_cancel_pending_for_cwd_calls_mirror_once_with_combined_delta(self):
		async def run():
			calls = []
			r = Registry()
			r.set_pending_mirror(lambda cwd, delta: calls.append((cwd, delta)))
			r.add(cwd="c:/work/sw", sender="A", request_id="r1")
			r.add(cwd="c:/work/sw", sender="B", request_id="r2")
			r.add(cwd="c:/work/other", sender="C", request_id="r3")
			calls.clear()
			r.cancel_pending_for_cwd("c:/work/sw")
			assert calls == [("c:/work/sw", -2)]
		asyncio.run(run())


class TestAwayModeCache:
	def test_cache_starts_empty_when_no_path(self):
		async def run():
			r = Registry()
			assert r.global_away() is False
			assert r.cwd_overrides() == {}
		asyncio.run(run())

	def test_update_global_away_cache(self):
		async def run():
			r = Registry()
			r.update_global_away_cache(True)
			assert r.global_away() is True
			r.update_global_away_cache(False)
			assert r.global_away() is False
		asyncio.run(run())

	def test_update_cwd_override_cache_set_and_remove(self):
		async def run():
			r = Registry()
			r.update_cwd_override_cache("c:/work/sw", True)
			assert r.cwd_overrides() == {"c:/work/sw": True}
			r.update_cwd_override_cache("c:/work/sw", False)
			assert r.cwd_overrides() == {"c:/work/sw": False}
			r.update_cwd_override_cache("c:/work/sw", None)
			assert r.cwd_overrides() == {}
		asyncio.run(run())


class TestSnapshotLoad:
	def test_listener_callbacks_populate_cache(self):
		"""Smoke: simulate a snapshot load by calling update_*_cache directly,
		verify the cache reflects the values."""
		async def run():
			r = Registry()
			r.update_global_away_cache(True)
			r.update_cwd_override_cache("c:/work/a", True)
			r.update_cwd_override_cache("c:/work/b", False)
			assert r.global_away() is True
			assert r.cwd_overrides() == {"c:/work/a": True, "c:/work/b": False}
		asyncio.run(run())
