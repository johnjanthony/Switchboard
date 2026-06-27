from __future__ import annotations

import json


def _canonical(value) -> str:
	# Order-insensitive, type-stable serialization so re-pushes of identical data
	# do not register as changes (key order, int vs float-from-json, etc.).
	return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class WidgetSnapshotStore:
	"""In-memory holder for the latest widget snapshot pushed by Watchtower.

	Diffs each push against the last-published rings/quota so the caller writes
	RTDB only when something actually changed. Not persisted: a restart simply
	waits for the next push."""

	def __init__(self) -> None:
		self.rings: dict = {}
		self.quota: dict | None = None
		self.pushed_at: str | None = None
		self._rings_canon: str | None = None
		self._quota_canon: str | None = None

	def apply(self, rings: dict, quota: dict | None, pushed_at: str | None) -> tuple[bool, bool]:
		rings = rings or {}
		rings_canon = _canonical(rings)
		quota_canon = _canonical(quota)
		rings_changed = rings_canon != self._rings_canon
		quota_changed = quota_canon != self._quota_canon
		self.rings = rings
		self.quota = quota
		self.pushed_at = pushed_at
		self._rings_canon = rings_canon
		self._quota_canon = quota_canon
		return rings_changed, quota_changed
