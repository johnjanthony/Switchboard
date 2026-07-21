from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server.gateway.bg_tasks import _spawn_bg

SUMMARY_URL = "https://status.claude.com/api/v2/summary.json"
WATCH_INTERVAL_SECONDS = 30
MAX_WATCH_MINUTES = 180

# Incident statuses meaning "no longer active".
_CLOSED = {"resolved", "postmortem", "completed"}
_DEGRADED = {"minor", "major", "critical"}
_INDICATOR_TO_LEVEL = {
	"none": "operational",
	"minor": "minor",
	"minor_outage": "minor",
	"degraded_performance": "minor",
	"partial_outage": "minor",
	"major": "major",
	"major_outage": "major",
	"critical": "critical",
	"critical_outage": "critical",
}


@dataclass(frozen=True)
class ClaudeStatus:
	level: str                  # operational|minor|major|critical|unknown
	description: str
	incidents: list[str]
	fetched_at: datetime


def parse_status(json_text: str, fetched_at: datetime) -> ClaudeStatus | None:
	"""Parse summary.json. None on malformed JSON or a missing/ill-typed
	status.indicator; a present-but-unrecognized indicator -> level 'unknown'."""
	try:
		root = json.loads(json_text)
	except (ValueError, TypeError):
		return None
	if not isinstance(root, dict):
		return None
	status = root.get("status")
	if not isinstance(status, dict):
		return None
	indicator = status.get("indicator")
	if not isinstance(indicator, str):
		return None
	level = _INDICATOR_TO_LEVEL.get(indicator, "unknown")
	description = status.get("description") if isinstance(status.get("description"), str) else ""
	incidents: list[str] = []
	highest_impact = "none"
	impact_rank = {"critical": 3, "major": 2, "minor": 1, "none": 0}

	arr = root.get("incidents")
	if isinstance(arr, list):
		for inc in arr:
			if not isinstance(inc, dict):
				continue
			if str(inc.get("status", "")).lower() in _CLOSED:
				continue
			name = inc.get("name")
			if isinstance(name, str) and name:
				incidents.append(name)
				impact = str(inc.get("impact", "")).lower()
				if impact_rank.get(impact, 0) > impact_rank.get(highest_impact, 0):
					highest_impact = impact

	if incidents and level == "operational":
		if highest_impact in _INDICATOR_TO_LEVEL and highest_impact != "none":
			level = _INDICATOR_TO_LEVEL[highest_impact]
		else:
			level = "minor"

	return ClaudeStatus(level=level, description=description, incidents=incidents, fetched_at=fetched_at)


def unknown_status(fetched_at: datetime) -> ClaudeStatus:
	return ClaudeStatus("unknown", "Status unavailable", [], fetched_at)


class ClaudeStatusWatch:
	"""Watch-until-resolved state machine, ported from T-179. Pure: the caller owns
	the fetch and clock. The dot stays visible from the moment a watch begins until
	acknowledge; polling stops on operational, the cap, or acknowledge."""

	def __init__(self, max_watch_minutes: int = MAX_WATCH_MINUTES) -> None:
		self._max = max(1, max_watch_minutes)
		self._state = "idle"
		self._last: ClaudeStatus | None = None
		self._watch_start: datetime | None = None

	@property
	def state(self) -> str:
		return self._state

	def apply_fetch(self, status: ClaudeStatus, now: datetime) -> str:
		self._last = status
		if self._state == "idle":
			if status.level in _DEGRADED:
				self._state = "watching"
				self._watch_start = now
				return "start_polling"
			return "none"
		if self._state == "watching":
			if status.level == "operational":
				self._state = "resolved_unacked"
				return "stop_polling"
			if self._watch_start is not None and now - self._watch_start >= timedelta(minutes=self._max):
				self._state = "capped_unacked"
				return "stop_polling"
			return "none"
		return "none"

	def acknowledge(self) -> str:
		if self._state == "idle":
			return "none"
		self._state = "idle"
		self._watch_start = None
		return "stop_polling"

	def snapshot(self) -> dict:
		if self._state == "resolved_unacked":
			dot_level = "operational"
		elif self._state in ("watching", "capped_unacked"):
			dot_level = self._last.level if self._last else "unknown"
		else:
			dot_level = "operational"
		button = {"watching": "stop", "resolved_unacked": "clear", "capped_unacked": "clear"}.get(self._state, "check")
		return {
			"watch_state": self._state,
			"dot_visible": self._state != "idle",
			"level": dot_level,
			"has_data": self._last is not None,
			"description": self._last.description if self._last else "",
			"incidents": list(self._last.incidents) if self._last else [],
			"fetched_at": self._last.fetched_at.isoformat() if self._last else None,
			"button": button,
		}


def _http_get(url: str, timeout: float = 10.0) -> str:
	with urllib.request.urlopen(url, timeout=timeout) as resp:
		return resp.read().decode("utf-8", errors="replace")


async def _default_fetch() -> "ClaudeStatus":
	"""Fetch + parse summary.json off the event loop. Any failure collapses to an
	unknown status (never raises to the caller), matching T-179's reader."""
	now = datetime.now(timezone.utc)
	try:
		text = await asyncio.to_thread(_http_get, SUMMARY_URL)
	except Exception:
		return unknown_status(now)
	return parse_status(text, now) or unknown_status(now)


class ClaudeStatusService:
	"""Server-global owner of Claude status. Fetches only on request; runs the
	watch-until-resolved loop as a background task while watching; publishes the
	view via `publish` on every change."""

	def __init__(self, publish, fetch=None, watch=None, interval_seconds: int = WATCH_INTERVAL_SECONDS, spawn=None) -> None:
		self._publish = publish
		self._fetch = fetch or _default_fetch
		self._watch = watch or ClaudeStatusWatch()
		self._interval = max(0, interval_seconds)
		self._spawn = spawn or _spawn_bg
		self._task: asyncio.Task | None = None

	def view(self) -> dict:
		return self._watch.snapshot()

	async def check(self) -> dict:
		"""One-shot fetch from Idle (or a manual re-check). Starts the poll loop if
		this transitions into Watching."""
		status = await self._fetch()
		action = self._watch.apply_fetch(status, datetime.now(timezone.utc))
		await self._publish_view()
		self._react(action)
		return self._watch.snapshot()

	async def stop(self) -> dict:
		"""Acknowledge / stop watching -> Idle."""
		action = self._watch.acknowledge()
		self._react(action)
		await self._publish_view()
		return self._watch.snapshot()

	def _react(self, action: str) -> None:
		if action == "start_polling":
			self._start_loop()
		elif action == "stop_polling":
			self._stop_loop()

	def _start_loop(self) -> None:
		if self._task is None or self._task.done():
			self._task = self._spawn(self._poll_loop(), label="claude_status_watch")

	def _stop_loop(self) -> None:
		if self._task is not None and not self._task.done():
			self._task.cancel()
		self._task = None

	async def _poll_loop(self) -> None:
		try:
			while True:
				await asyncio.sleep(self._interval)
				status = await self._fetch()
				action = self._watch.apply_fetch(status, datetime.now(timezone.utc))
				await self._publish_view()
				if action == "stop_polling":
					break
		except asyncio.CancelledError:
			pass

	async def _publish_view(self) -> None:
		await self._publish(self._watch.snapshot())
