from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server.gateway.bg_tasks import _spawn_bg

GCP_INCIDENTS_URL = "https://status.cloud.google.com/incidents.json"
WATCH_INTERVAL_SECONDS = 30
MAX_WATCH_MINUTES = 180

_CLOSED_STATUSES = {"resolved", "completed", "closed"}
_DEGRADED_LEVELS = {"minor", "major", "critical"}

# Keywords that mark a GCP incident as relevant to Antigravity / Gemini / Vertex AI
_AI_KEYWORDS = {
	"generative ai",
	"vertex ai",
	"gemini",
	"ai platform",
	"cloud ai",
	"duet ai",
	"language server",
	"antigravity",
}


@dataclass(frozen=True)
class AntigravityStatus:
	level: str  # operational|minor|major|critical|unknown
	description: str
	incidents: list[str]
	fetched_at: datetime
	local_lsp_healthy: bool = True


def parse_gcp_incidents(
	json_text: str,
	fetched_at: datetime,
	local_lsp_healthy: bool = True,
) -> AntigravityStatus | None:
	"""Parse Google Cloud status incidents.json. None on malformed JSON or invalid root.
	Filters active incidents affecting Generative AI / Vertex AI / Cloud AI services,
	or factors in local LSP unreachability when local_lsp_healthy is False."""
	try:
		root = json.loads(json_text)
	except (ValueError, TypeError):
		return None

	if not isinstance(root, list):
		return None

	active_incidents: list[dict] = []
	incident_names: list[str] = []
	highest_severity = "none"
	severity_rank = {"critical": 3, "major": 2, "minor": 1, "none": 0}

	for inc in root:
		if not isinstance(inc, dict):
			continue
		# Check if closed
		status_impact = str(inc.get("status_impact", "")).lower()
		if inc.get("resolved") is True or status_impact in _CLOSED_STATUSES:
			continue
		if inc.get("end"):
			continue

		service_name = str(inc.get("service_name", "")).lower()
		service_key = str(inc.get("service_key", "")).lower()
		summary = str(inc.get("summary", "")).lower()

		# Check relevance to AI / Gemini / Vertex AI
		is_relevant = any(
			kw in service_name or kw in service_key or kw in summary
			for kw in _AI_KEYWORDS
		)
		if not is_relevant:
			continue

		title = inc.get("external_desc") or inc.get("summary") or service_name
		if isinstance(title, str) and title:
			incident_names.append(title)
			active_incidents.append(inc)

			severity = "minor"
			if "critical" in status_impact or "outage" in status_impact:
				severity = "critical"
			elif "major" in status_impact or "disruption" in status_impact:
				severity = "major"

			if severity_rank.get(severity, 0) > severity_rank.get(highest_severity, 0):
				highest_severity = severity

	# Combine local LSP health and cloud incidents
	if not local_lsp_healthy:
		incident_names.insert(0, "Local Antigravity Language Server unreachable")
		if highest_severity == "none":
			highest_severity = "minor"
		desc = "Local LSP unreachable"
		if active_incidents:
			desc += f" ({len(active_incidents)} cloud incident(s))"
	elif active_incidents:
		desc = f"Google Cloud: {len(active_incidents)} active incident(s)"
	else:
		desc = "All Systems Operational"

	level = "operational" if highest_severity == "none" else highest_severity

	return AntigravityStatus(
		level=level,
		description=desc,
		incidents=incident_names,
		fetched_at=fetched_at,
		local_lsp_healthy=local_lsp_healthy,
	)


def unknown_antigravity_status(
	fetched_at: datetime,
	local_lsp_healthy: bool = True,
) -> AntigravityStatus:
	desc = (
		"Status unavailable (Local LSP unreachable)"
		if not local_lsp_healthy
		else "Status unavailable"
	)
	incidents = (
		["Local Antigravity Language Server unreachable"]
		if not local_lsp_healthy
		else []
	)
	level = "minor" if not local_lsp_healthy else "unknown"
	return AntigravityStatus(
		level=level,
		description=desc,
		incidents=incidents,
		fetched_at=fetched_at,
		local_lsp_healthy=local_lsp_healthy,
	)


class AntigravityStatusWatch:
	"""Watch-until-resolved state machine for Antigravity backend status.
	Matches ClaudeStatusWatch behavior."""

	def __init__(self, max_watch_minutes: int = MAX_WATCH_MINUTES) -> None:
		self._max = max(1, max_watch_minutes)
		self._state = "idle"
		self._last: AntigravityStatus | None = None
		self._watch_start: datetime | None = None

	@property
	def state(self) -> str:
		return self._state

	def apply_fetch(self, status: AntigravityStatus, now: datetime) -> str:
		self._last = status
		if self._state == "idle":
			if status.level in _DEGRADED_LEVELS:
				self._state = "watching"
				self._watch_start = now
				return "start_polling"
			return "none"
		if self._state == "watching":
			if status.level == "operational":
				self._state = "resolved_unacked"
				return "stop_polling"
			if (
				self._watch_start is not None
				and now - self._watch_start >= timedelta(minutes=self._max)
			):
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
		button = {
			"watching": "stop",
			"resolved_unacked": "clear",
			"capped_unacked": "clear",
		}.get(self._state, "check")
		return {
			"watch_state": self._state,
			"dot_visible": self._state != "idle",
			"level": dot_level,
			"has_data": self._last is not None,
			"description": self._last.description if self._last else "",
			"incidents": list(self._last.incidents) if self._last else [],
			"fetched_at": self._last.fetched_at.isoformat() if self._last else None,
			"button": button,
			"local_lsp_healthy": self._last.local_lsp_healthy if self._last else True,
		}


def _http_get(url: str, timeout: float = 10.0) -> str:
	with urllib.request.urlopen(url, timeout=timeout) as resp:
		return resp.read().decode("utf-8", errors="replace")


async def _default_fetch() -> AntigravityStatus:
	now = datetime.now(timezone.utc)
	try:
		text = await asyncio.to_thread(_http_get, GCP_INCIDENTS_URL)
	except Exception:
		return unknown_antigravity_status(now)
	return parse_gcp_incidents(text, now) or unknown_antigravity_status(now)


class AntigravityStatusService:
	"""Server-global owner of Antigravity status. Fetches on request, runs watch
	loop, and publishes status views on state change."""

	def __init__(
		self,
		publish,
		fetch=None,
		watch=None,
		interval_seconds: int = WATCH_INTERVAL_SECONDS,
		spawn=None,
	) -> None:
		self._publish = publish
		self._fetch = fetch or _default_fetch
		self._watch = watch or AntigravityStatusWatch()
		self._interval = max(0, interval_seconds)
		self._spawn = spawn or _spawn_bg
		self._task: asyncio.Task | None = None

	def view(self) -> dict:
		return self._watch.snapshot()

	async def check(self) -> dict:
		status = await self._fetch()
		action = self._watch.apply_fetch(status, datetime.now(timezone.utc))
		await self._publish_view()
		self._react(action)
		return self._watch.snapshot()

	async def stop(self) -> dict:
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
			self._task = self._spawn(
				self._poll_loop(), label="antigravity_status_watch"
			)

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
