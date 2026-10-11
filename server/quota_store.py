"""QuotaStore: the account's Claude plan usage as the sessions' mods last
reported it (session.measure rateLimits). One reading for the whole account,
mirrored to RTDB `quota`, so no consumer polls the usage endpoint itself."""

from __future__ import annotations

import copy
from datetime import datetime

from server.telemetry import QUOTA_KINDS

# A report carrying rate limits, which the mod sends only after a model
# request, confirms the stored reading even when they have not moved; publishing
# that confirmation at most once a minute keeps an active session to one write.
CONFIRM_PUBLISH_SECONDS = 60.0


class QuotaStore:
	"""received_at is when a session last confirmed the reading: a value change,
	or a report carrying rate limits that had not moved (the mod sends them only
	after a model request)."""

	def __init__(self) -> None:
		self._windows: dict[str, dict] = {}
		self._received_at: str | None = None
		self._published_ts = 0.0

	def apply(self, rate_limits: list[dict], received_at: str) -> bool:
		"""Replace each window the report carries and leave the others. Returns
		whether the reading changed; an identical report is not a change, so it
		never re-writes RTDB and received_at keeps the time of the last change."""
		changed = False
		for item in rate_limits:
			window = {"percent_used": item["percent_used"], "resets_at": item.get("resets_at")}
			if self._windows.get(item["kind"]) != window:
				self._windows[item["kind"]] = window
				changed = True
		if changed:
			self._received_at = received_at
		return changed

	def confirm(self, received_at: str, now_ts: float) -> bool:
		"""Record that a session's measurement confirmed the stored reading, so
		received_at means "last confirmed". Returns whether to publish now: at
		most once per CONFIRM_PUBLISH_SECONDS. Before any reading there is
		nothing to confirm."""
		if not self._windows:
			return False
		self._received_at = received_at
		return now_ts - self._published_ts >= CONFIRM_PUBLISH_SECONDS

	def mark_published(self, now_ts: float) -> None:
		self._published_ts = now_ts

	def expire(self, now_ts: float) -> bool:
		"""Zero each window whose reset time has passed with no newer reading, so
		every consumer shows the same thing without its own reset logic."""
		changed = False
		for window in self._windows.values():
			resets_at = window.get("resets_at")
			if resets_at is None:
				continue
			try:
				reset_ts = datetime.fromisoformat(resets_at).timestamp()
			except ValueError:
				continue
			if reset_ts <= now_ts:
				window["percent_used"] = 0
				window["resets_at"] = None
				changed = True
		return changed

	def load(self, published: dict | None) -> None:
		"""Start from the reading last published to RTDB, so a restart keeps
		showing it and still zeroes it when its reset passes."""
		if not isinstance(published, dict):
			return
		for kind in QUOTA_KINDS:
			window = published.get(kind)
			if not isinstance(window, dict):
				continue
			used = window.get("percent_used")
			if not isinstance(used, (int, float)) or isinstance(used, bool):
				continue
			resets_at = window.get("resets_at")
			self._windows[kind] = {"percent_used": used, "resets_at": resets_at if isinstance(resets_at, str) else None}
		received_at = published.get("received_at")
		self._received_at = received_at if isinstance(received_at, str) else None

	def snapshot(self) -> dict | None:
		if not self._windows:
			return None
		out: dict = copy.deepcopy(self._windows)
		out["received_at"] = self._received_at
		return out
