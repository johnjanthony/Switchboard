"""Supervisors for Firebase RTDB listeners and dispatch loops.

Background: `firebase_admin/db.py:121` spawns a `threading.Thread` per
`Reference.listen()` call. If the SSE iterator inside `_start_listen` raises
(DNS failure, OAuth refresh transport error, etc.), the thread dies silently —
the SDK has no error callback. The original Switchboard code stored the
returned `ListenerRegistration` and assumed it stayed alive forever.

`SupervisedListener` wraps a single listener with:
- a callback shim that records `last_event_at` on every fire
- an asyncio watchdog that polls `registration._thread.is_alive()` every
  `watchdog_interval_seconds` and reconnects with exponential backoff on death
- crash counters (`crash_count`, `last_crash_at`) and the running `state`
  (`starting`, `live`, `reconnecting`, `stopped`) for `/healthz` reporting

`LoopSupervisor` is the matching primitive for the four async dispatch loops
in `gateway/dispatch.py` — same crash-counter semantics, plus a doubling
alert threshold so sustained outages produce ongoing visibility, capped by
a 10-minute wall-clock gate so a permanently-broken loop stops paging.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from firebase_admin import db


_BACKOFF_INITIAL_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 60.0
_DEFAULT_WATCHDOG_INTERVAL_SECONDS = 5.0
_ALERT_SUPPRESS_AFTER_SECONDS = 600.0  # 10 minutes


async def _safe_error_log(error_logger, msg: str) -> None:
	"""Await error_logger without letting its failure kill the supervision path.

	The supervisors exist to survive crashes; a logging failure (disk full,
	file locked by AV) must degrade to stdlib logging, never propagate into
	the loop being supervised. CancelledError still propagates - shutdown
	semantics are unchanged."""
	try:
		await error_logger(msg)
	except Exception:
		logging.getLogger(__name__).exception("error_logger failed while reporting: %s", msg)


@dataclass
class ListenerHealth:
	"""Snapshot of a SupervisedListener's state for /healthz."""

	name: str
	state: str  # "starting" | "live" | "reconnecting" | "stopped"
	last_event_at: Optional[float]  # monotonic seconds; None until first event
	crash_count: int
	last_crash_at: Optional[float]


class SupervisedListener:
	"""Owns one Firebase listener, restarts it on death.

	Lifecycle:
	  start() -> running supervisor + listen() registration
	  stop()  -> watchdog cancelled, registration closed

	The supervisor task runs forever until stop() is called. It calls
	`db.reference(path).listen(wrapped_cb)` once initially and then polls
	the registration's thread liveness on every watchdog tick. On death:
	close the dead registration (best-effort), sleep with backoff, listen
	again. Each detected death increments crash_count; consecutive
	successful watchdog ticks reset the backoff to its initial value.

	The wrapped callback updates last_event_at on every event. Unhandled
	exceptions raised by the user callback are caught, logged via the
	supplied error_logger, and counted as a crash.
	"""

	def __init__(
		self,
		name: str,
		path: str,
		callback: Callable[[Any], None],
		error_logger: Callable[[str], Awaitable[None]],
		loop: asyncio.AbstractEventLoop,
		watchdog_interval_seconds: float = _DEFAULT_WATCHDOG_INTERVAL_SECONDS,
	) -> None:
		self._name = name
		self._path = path
		self._user_callback = callback
		self._error_logger = error_logger
		self._loop = loop
		self._watchdog_interval = watchdog_interval_seconds

		self._registration: Optional[db.ListenerRegistration] = None
		self._supervisor_task: Optional[asyncio.Task] = None
		self._state = "stopped"
		self._last_event_at: Optional[float] = None
		self._crash_count = 0
		self._last_crash_at: Optional[float] = None
		self._stopping = False

	@property
	def name(self) -> str:
		return self._name

	def start(self) -> None:
		"""Begin supervised listening. Idempotent — second call is a no-op."""
		if self._supervisor_task is not None:
			return
		self._stopping = False
		self._supervisor_task = self._loop.create_task(
			self._supervise(), name=f"supervisor:{self._name}"
		)

	async def stop(self) -> None:
		"""Stop the watchdog and close the registration."""
		self._stopping = True
		if self._supervisor_task is not None:
			self._supervisor_task.cancel()
			try:
				await self._supervisor_task
			except asyncio.CancelledError:
				pass
			self._supervisor_task = None
		await self._close_registration()
		self._state = "stopped"

	def health(self) -> ListenerHealth:
		return ListenerHealth(
			name=self._name,
			state=self._state,
			last_event_at=self._last_event_at,
			crash_count=self._crash_count,
			last_crash_at=self._last_crash_at,
		)

	def _wrapped_callback(self, event: Any) -> None:
		# Runs in the SDK's listener thread. Update timestamp first so that
		# even if the user callback raises, we still recorded liveness.
		self._last_event_at = time.monotonic()
		try:
			self._user_callback(event)
		except Exception as exc:
			# Deferred import: a module-level import would cycle through
			# server.gateway.__init__ -> dispatch -> this module (mirror of
			# firebase.py's deferred SupervisedListener imports).
			from server.gateway.bg_tasks import _spawn_bg
			# Schedule the error log on the event loop; we're off-loop here.
			# Capture exc as a default arg to avoid the late-binding closure trap —
			# by the time the lambda fires, the except block scope is gone.
			self._loop.call_soon_threadsafe(
				lambda _exc=exc: _spawn_bg(
					self._error_logger(
						f"listener_callback_error:{self._name}: {_exc}"
					),
					label=f"listener_callback_error:{self._name}",
				)
			)

	def _open_registration(self) -> None:
		# Synchronous wrapper around db.reference(path).listen(cb). Runs in
		# whatever thread (or async loop) is calling it; the SDK spawns its
		# own thread internally.
		self._registration = db.reference(self._path).listen(self._wrapped_callback)

	async def _close_registration(self) -> None:
		reg = self._registration
		self._registration = None
		if reg is None:
			return
		# .close() blocks for many seconds while the SSE stream tears down;
		# offload to the default executor.
		try:
			await self._loop.run_in_executor(None, reg.close)
		except Exception as exc:
			await _safe_error_log(
				self._error_logger, f"listener_close_error:{self._name}: {exc}"
			)

	def _registration_alive(self) -> bool:
		reg = self._registration
		if reg is None:
			return False
		thread = getattr(reg, "_thread", None)
		if thread is None:
			# SDK shape changed; treat as alive to avoid spurious restarts
			# (and surface via crash count if the listener really is dead).
			return True
		return thread.is_alive()

	async def _supervise(self) -> None:
		"""Main supervisor loop. Runs until stop() cancels it."""
		backoff = _BACKOFF_INITIAL_SECONDS
		# Initial connect.
		self._state = "starting"
		try:
			await self._loop.run_in_executor(None, self._open_registration)
			self._state = "live"
			backoff = _BACKOFF_INITIAL_SECONDS
		except Exception as exc:
			self._crash_count += 1
			self._last_crash_at = time.monotonic()
			self._state = "reconnecting"
			await _safe_error_log(
				self._error_logger, f"listener_initial_connect_failed:{self._name}: {exc}"
			)

		while not self._stopping:
			try:
				await asyncio.sleep(self._watchdog_interval)
			except asyncio.CancelledError:
				return

			if self._stopping:
				return

			if self._registration_alive():
				# Healthy tick — reset backoff so the next death starts
				# at the initial interval again.
				if self._state != "live":
					self._state = "live"
				backoff = _BACKOFF_INITIAL_SECONDS
				continue

			# Listener thread died (or never opened). Reconnect.
			self._crash_count += 1
			self._last_crash_at = time.monotonic()
			self._state = "reconnecting"
			await _safe_error_log(
				self._error_logger,
				f"listener_died:{self._name} (crash_count={self._crash_count}, "
				f"backoff={backoff:.1f}s)",
			)
			await self._close_registration()

			try:
				await asyncio.sleep(backoff)
			except asyncio.CancelledError:
				return
			backoff = min(backoff * 2.0, _BACKOFF_MAX_SECONDS)

			try:
				await self._loop.run_in_executor(None, self._open_registration)
				self._state = "live"
			except Exception as exc:
				await _safe_error_log(
					self._error_logger, f"listener_reconnect_failed:{self._name}: {exc}"
				)
				# Stay in `reconnecting` and try again on next tick.


@dataclass
class LoopHealth:
	"""Snapshot of a dispatch-loop supervisor's state for /healthz."""

	name: str
	consecutive_failures: int
	crash_count: int
	last_crash_at: Optional[float]


class LoopSupervisor:
	"""Replacement for `_loop_crash_backoff`.

	Each dispatch loop owns one. Call `record_success()` on every
	successful iteration; call `await record_crash(exc)` from the
	loop's outermost `except Exception` block to log + sleep with
	exponential backoff.

	Cadence-based alerting: the first alert fires when consecutive
	failures hit `initial_alert_threshold` (default 5); subsequent alerts
	fire at 10, 20, 40, ... — doubling each time. A successful iteration
	resets both the failure count and the alert threshold.

	Wall-clock cap: alerts are suppressed once
	`time.monotonic() - first_failure_at > _ALERT_SUPPRESS_AFTER_SECONDS`
	(10 min). The loop is clearly not going to recover on its own past
	that point; continued paging adds no operator value. On
	`record_success` the wall-clock anchor resets, so a subsequent outage
	starts the cadence fresh.
	"""

	def __init__(
		self,
		name: str,
		backend: Any,
		error_logger: Callable[[str], Awaitable[None]],
		initial_alert_threshold: int = 5,
	) -> None:
		self._name = name
		self._backend = backend
		self._error_logger = error_logger
		self._initial_alert_threshold = initial_alert_threshold

		self._consecutive_failures = 0
		self._crash_count = 0
		self._last_crash_at: Optional[float] = None
		self._first_failure_at: Optional[float] = None
		self._backoff = _BACKOFF_INITIAL_SECONDS
		self._next_alert_at = initial_alert_threshold

	@property
	def name(self) -> str:
		return self._name

	def record_success(self) -> None:
		self._consecutive_failures = 0
		self._backoff = _BACKOFF_INITIAL_SECONDS
		self._next_alert_at = self._initial_alert_threshold
		self._first_failure_at = None

	async def record_crash(self, exc: BaseException) -> None:
		now = time.monotonic()
		if self._first_failure_at is None:
			self._first_failure_at = now
		self._consecutive_failures += 1
		self._crash_count += 1
		self._last_crash_at = now

		await _safe_error_log(
			self._error_logger,
			f"{self._name}_loop_crashed: {exc} "
			f"(count={self._consecutive_failures}, "
			f"sleep={self._backoff:.1f}s)",
		)

		if self._consecutive_failures >= self._next_alert_at:
			elapsed = now - self._first_failure_at
			if elapsed <= _ALERT_SUPPRESS_AFTER_SECONDS:
				try:
					await self._backend.send_text(
						f"Switchboard {self._name} loop has failed "
						f"{self._consecutive_failures} times in a row — check service logs."
					)
				except Exception as alert_exc:
					await _safe_error_log(
						self._error_logger, f"{self._name}_loop_alert_failed: {alert_exc}"
					)
			# Always advance the threshold so we don't re-alert at the same
			# count on the next crash. Once `elapsed` passes the cap, all
			# subsequent thresholds are silently passed; recovery resets.
			self._next_alert_at *= 2

		await asyncio.sleep(self._backoff)
		self._backoff = min(self._backoff * 2.0, _BACKOFF_MAX_SECONDS)

	def health(self) -> LoopHealth:
		return LoopHealth(
			name=self._name,
			consecutive_failures=self._consecutive_failures,
			crash_count=self._crash_count,
			last_crash_at=self._last_crash_at,
		)
