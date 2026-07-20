"""Tests for SupervisedListener and LoopSupervisor."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.firebase_supervisor import LoopSupervisor, SupervisedListener


class _FakeRegistration:
	"""Stand-in for firebase_admin.db.ListenerRegistration.

	Owns a real threading.Thread so SupervisedListener._registration_alive()
	(which calls registration._thread.is_alive()) returns the right value.
	The thread parks on a threading.Event we control, so the test can simulate
	listener death by setting the event.
	"""

	def __init__(self) -> None:
		self._stop = threading.Event()
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()
		self.closed = False

	def _run(self) -> None:
		self._stop.wait()

	def kill(self) -> None:
		"""Simulate the SSE thread dying mid-iteration."""
		self._stop.set()
		self._thread.join(timeout=1.0)

	def close(self) -> None:
		self._stop.set()
		self._thread.join(timeout=1.0)
		self.closed = True


@pytest.fixture
def fake_registrations(monkeypatch):
	"""Patch db.reference(...).listen(...) to hand out _FakeRegistration objects.

	Each call to .listen() appends a new fake to the returned list so tests can
	simulate consecutive death+reconnect cycles."""
	created: list[_FakeRegistration] = []
	last_callback: list = [None]

	def _listen(callback):
		fake = _FakeRegistration()
		created.append(fake)
		last_callback[0] = callback
		return fake

	def _reference(_path):
		ref = MagicMock()
		ref.listen.side_effect = _listen
		return ref

	import server.firebase_supervisor as mod

	monkeypatch.setattr(mod.db, "reference", _reference)
	return created, last_callback


def _logger_collector():
	messages: list[str] = []

	async def _log(msg: str) -> None:
		messages.append(msg)

	return messages, _log


@pytest.mark.asyncio
async def test_supervised_listener_records_event_timestamp(fake_registrations):
	registrations, last_callback = fake_registrations
	messages, log = _logger_collector()
	loop = asyncio.get_running_loop()

	received = []
	supervisor = SupervisedListener(
		name="test",
		path="some/path",
		callback=lambda ev: received.append(ev),
		error_logger=log,
		loop=loop,
		watchdog_interval_seconds=0.05,
	)
	supervisor.start()
	# Let the initial connect run.
	await asyncio.sleep(0.1)

	assert len(registrations) == 1
	assert supervisor.health().state == "live"
	assert supervisor.health().last_event_at is None

	# Fire a synthetic event from "the SDK thread" (the callback runs there).
	last_callback[0]("event-1")
	assert received == ["event-1"]
	assert supervisor.health().last_event_at is not None

	await supervisor.stop()
	assert registrations[0].closed


@pytest.mark.asyncio
async def test_supervised_listener_reconnects_after_thread_death(fake_registrations):
	registrations, _ = fake_registrations
	messages, log = _logger_collector()
	loop = asyncio.get_running_loop()

	supervisor = SupervisedListener(
		name="resp",
		path="responses",
		callback=lambda ev: None,
		error_logger=log,
		loop=loop,
		watchdog_interval_seconds=0.05,
	)
	supervisor.start()
	await asyncio.sleep(0.1)
	assert supervisor.health().state == "live"
	assert supervisor.health().crash_count == 0

	# Kill the SDK thread to simulate the DNS / OAuth failure case.
	registrations[0].kill()

	# Watchdog should detect the death within a few ticks and reconnect.
	# Initial backoff is 1.0s.
	for _ in range(40):  # 40 × 0.05s = 2.0s
		await asyncio.sleep(0.05)
		if len(registrations) >= 2 and supervisor.health().state == "live":
			break

	health = supervisor.health()
	assert health.crash_count == 1, f"expected 1 crash, saw {health.crash_count}"
	assert health.last_crash_at is not None
	assert health.state == "live"
	assert any("listener_died:resp" in m for m in messages)

	await supervisor.stop()


@pytest.mark.asyncio
async def test_supervised_listener_swallows_user_callback_exceptions(fake_registrations):
	registrations, last_callback = fake_registrations
	messages, log = _logger_collector()
	loop = asyncio.get_running_loop()

	def _bad_callback(_event):
		raise RuntimeError("boom")

	supervisor = SupervisedListener(
		name="bad",
		path="x",
		callback=_bad_callback,
		error_logger=log,
		loop=loop,
		watchdog_interval_seconds=0.05,
	)
	supervisor.start()
	await asyncio.sleep(0.1)

	# Fire — the SDK thread is the one that would raise, but our wrapper
	# catches it. The supervisor stays live, and the error is logged.
	last_callback[0]("event")
	# Give the call_soon_threadsafe-scheduled log task a tick.
	await asyncio.sleep(0.05)

	assert supervisor.health().state == "live"
	assert any("listener_callback_error:bad" in m for m in messages)

	await supervisor.stop()


@pytest.mark.asyncio
async def test_listener_callback_error_log_routes_through_spawn_bg(fake_registrations, monkeypatch):
	"""REV-105: the off-thread error-log bounce must go through _spawn_bg
	(strong ref + logged failure), not a bare loop.create_task the loop only
	weak-references."""
	import server.gateway.bg_tasks as bg_tasks_mod

	registrations, last_callback = fake_registrations
	messages, log = _logger_collector()
	loop = asyncio.get_running_loop()

	labels: list[str] = []
	real_spawn = bg_tasks_mod._spawn_bg

	def _spy(coro, *, label):
		labels.append(label)
		return real_spawn(coro, label=label)

	monkeypatch.setattr(bg_tasks_mod, "_spawn_bg", _spy)

	def _bad_callback(_event):
		raise RuntimeError("boom")

	supervisor = SupervisedListener(
		name="bad2",
		path="x",
		callback=_bad_callback,
		error_logger=log,
		loop=loop,
		watchdog_interval_seconds=0.05,
	)
	supervisor.start()
	await asyncio.sleep(0.1)

	last_callback[0]("event")
	await asyncio.sleep(0.05)

	assert labels == ["listener_callback_error:bad2"]
	assert any("listener_callback_error:bad2" in m for m in messages)

	await supervisor.stop()


@pytest.mark.asyncio
async def test_loop_supervisor_resets_on_success():
	messages, log = _logger_collector()

	class _FakeBackend:
		async def send_text(self, _msg): pass

	sup = LoopSupervisor("dispatch_test", _FakeBackend(), log)
	# First crash.
	await sup.record_crash(RuntimeError("a"))
	assert sup.health().consecutive_failures == 1
	assert sup.health().crash_count == 1

	# Success — failures reset; crash_count is cumulative and stays.
	sup.record_success()
	assert sup.health().consecutive_failures == 0
	assert sup.health().crash_count == 1


@pytest.mark.asyncio
async def test_loop_supervisor_alerts_on_doubling_threshold(monkeypatch):
	messages, log = _logger_collector()
	calls: list[str] = []

	class _FakeBackend:
		async def send_text(self, msg: str) -> None:
			calls.append(msg)

	sup = LoopSupervisor(
		"dispatch_test", _FakeBackend(), log, initial_alert_threshold=2
	)
	# Patch out the actual sleep so the test runs fast.
	import server.firebase_supervisor as mod

	async def _no_sleep(_secs):
		return None

	monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

	# 1 crash — no alert (threshold = 2).
	await sup.record_crash(RuntimeError("1"))
	assert calls == []
	# 2nd crash — alerts.
	await sup.record_crash(RuntimeError("2"))
	assert len(calls) == 1
	# 3rd, 4th — no alert (next threshold = 4).
	await sup.record_crash(RuntimeError("3"))
	await sup.record_crash(RuntimeError("4"))
	assert len(calls) == 2  # 4th hit threshold 4
	# 5th, 6th, 7th — no alert (next threshold = 8).
	await sup.record_crash(RuntimeError("5"))
	await sup.record_crash(RuntimeError("6"))
	await sup.record_crash(RuntimeError("7"))
	assert len(calls) == 2
	# 8th — alerts.
	await sup.record_crash(RuntimeError("8"))
	assert len(calls) == 3


@pytest.mark.asyncio
async def test_loop_supervisor_suppresses_alerts_after_10_minutes(monkeypatch):
	"""Once 10 minutes have elapsed since first_failure_at, alerts are
	suppressed even when threshold is crossed. record_success resets
	the wall-clock anchor."""
	messages, log = _logger_collector()
	calls: list[str] = []

	class _FakeBackend:
		async def send_text(self, msg: str) -> None:
			calls.append(msg)

	sup = LoopSupervisor(
		"dispatch_test", _FakeBackend(), log, initial_alert_threshold=2
	)
	import server.firebase_supervisor as mod

	async def _no_sleep(_secs):
		return None

	monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

	# Drive a fake clock so we can step past the 10-minute cap deterministically.
	t = [1000.0]
	monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

	# First crash anchors first_failure_at = 1000.0.
	await sup.record_crash(RuntimeError("a"))
	# Second crash, still under cap — alert fires.
	await sup.record_crash(RuntimeError("b"))
	assert len(calls) == 1

	# Advance 11 minutes. Next alert threshold is 4.
	t[0] = 1000.0 + 660.0  # 11 min
	# Crashes 3 and 4 — 4th hits threshold 4 but should be suppressed.
	await sup.record_crash(RuntimeError("c"))
	await sup.record_crash(RuntimeError("d"))
	assert len(calls) == 1, f"alert fired past 10-min cap: {calls}"

	# Recovery resets first_failure_at and the threshold.
	sup.record_success()
	t[0] = 1000.0 + 700.0
	# New outage — alerts work again.
	await sup.record_crash(RuntimeError("e"))
	await sup.record_crash(RuntimeError("f"))
	assert len(calls) == 2, "second outage should produce a fresh alert"


def _raising_error_logger():
	async def _log(msg: str) -> None:
		raise OSError("disk full")
	return _log


@pytest.mark.asyncio
async def test_record_crash_survives_error_logger_failure():
	backend = MagicMock()
	backend.send_text = AsyncMock()
	sup = LoopSupervisor("t192", backend, _raising_error_logger(), initial_alert_threshold=1)
	sup._backoff = 0.0  # keep the test fast; backoff sleep still runs
	await sup.record_crash(RuntimeError("boom"))  # must NOT raise
	assert sup.health().crash_count == 1
	assert sup.health().consecutive_failures == 1


@pytest.mark.asyncio
async def test_record_crash_alert_failure_plus_logger_failure_does_not_raise():
	backend = MagicMock()
	backend.send_text = AsyncMock(side_effect=RuntimeError("fcm down"))
	sup = LoopSupervisor("t192", backend, _raising_error_logger(), initial_alert_threshold=1)
	sup._backoff = 0.0
	await sup.record_crash(RuntimeError("boom"))  # alert fails, its logger fails: still no raise
	assert sup.health().crash_count == 1


@pytest.mark.asyncio
async def test_supervise_initial_connect_failure_with_raising_logger_keeps_supervisor_alive():
	loop = asyncio.get_running_loop()
	listener = SupervisedListener(
		name="t192-listener",
		path="some/path",
		callback=lambda ev: None,
		error_logger=_raising_error_logger(),
		loop=loop,
		watchdog_interval_seconds=0.01,
	)
	with patch.object(listener, "_open_registration", side_effect=RuntimeError("no dns")):
		listener.start()
		await asyncio.sleep(0.05)
		# The supervisor task must still be running (reconnecting), not dead from the logger raise.
		assert listener._supervisor_task is not None and not listener._supervisor_task.done()
		assert listener.health().state == "reconnecting"
		await listener.stop()


@pytest.mark.asyncio
async def test_close_registration_swallows_logger_failure():
	loop = asyncio.get_running_loop()
	listener = SupervisedListener(
		name="t192-close",
		path="p",
		callback=lambda ev: None,
		error_logger=_raising_error_logger(),
		loop=loop,
	)
	reg = MagicMock()
	reg.close = MagicMock(side_effect=RuntimeError("teardown hang"))
	listener._registration = reg
	await listener._close_registration()  # close fails, logging it fails: no raise
