"""SessionRegistry: every Claude Code session on the workstation, birth to death.

Sessions are first-class entities keyed by cli_session_id (== Claude Code
session_id == transcript stem == the id `claude --resume` takes). The registry
is push-fed only: plugin hooks, Watchtower ring snapshots, and switchboard MCP
calls. The server runs as LocalSystem and cannot scan transcripts itself, so
nothing here reads the filesystem.

Same single-event-loop access model as Registry: no locking. RTDB mirroring
happens via the mirror callback (set by main.py), which diffs internally so
identical payloads do not re-write.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Callable

SESSION_STATES = ("active", "idle", "awaiting_human", "awaiting_agent", "ended", "lost")
TERMINAL_STATES = ("ended", "lost")
# Blocked-on-tool sessions are legitimately silent for hours; the pending
# future is the liveness proof, so the sweeper never marks them lost.
SWEEP_EXEMPT_STATES = ("awaiting_human", "awaiting_agent")


def _now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def map_hook_event_to_state(event: str, state: str) -> str | None:
	"""Translate an agent-status hook (event, display-state) pair into a registry
	state. The hook's display states (thinking/clear/waiting/tool:<n>) predate the
	registry and also drive the phone status row; this mapping is the only place
	the two vocabularies meet."""
	if event == "UserPromptSubmit":
		return "active"
	if event == "PostToolUse":
		return "active"
	if event == "Stop":
		return "idle"
	if event == "PreToolUse":
		if state == "clear":
			return "awaiting_human"
		if state == "waiting":
			return "awaiting_agent"
		return "active"
	return None


@dataclass
class SessionRecord:
	cli_session_id: str
	cwd: str = ""
	surface: str = "windows"
	cli: str = "claude"
	started_at: str = ""
	last_event_at: str = ""
	state: str = "idle"
	state_detail: str | None = None
	conversation_id: str | None = None
	sender: str | None = None
	model: str | None = None
	context_pct: float | None = None
	end_reason: str | None = None
	source: str = "hook"

	def to_payload(self) -> dict:
		return asdict(self)


class SessionRegistry:
	def __init__(self, now: Callable[[], str] = _now_iso) -> None:
		self._records: dict[str, SessionRecord] = {}
		self._now = now
		self._mirror: Callable[[str, dict | None], None] | None = None
		self._mirror_canon: dict[str, str] = {}

	# -- reads ------------------------------------------------------------

	def get(self, cli_session_id: str) -> SessionRecord | None:
		return self._records.get(cli_session_id)

	def snapshot(self) -> list[SessionRecord]:
		return sorted(self._records.values(), key=lambda r: r.last_event_at, reverse=True)

	def counts_by_state(self) -> dict[str, int]:
		counts: dict[str, int] = {}
		for r in self._records.values():
			counts[r.state] = counts.get(r.state, 0) + 1
		return counts

	# -- mirror -----------------------------------------------------------

	def set_mirror(self, callback: Callable[[str, dict | None], None]) -> None:
		"""callback(cli_session_id, payload_or_None). None means delete (prune)."""
		self._mirror = callback

	def _fire_mirror(self, record: SessionRecord) -> None:
		if self._mirror is None:
			return
		payload = record.to_payload()
		canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
		if self._mirror_canon.get(record.cli_session_id) == canon:
			return
		self._mirror_canon[record.cli_session_id] = canon
		try:
			self._mirror(record.cli_session_id, payload)
		except Exception:
			logging.getLogger(__name__).exception("session mirror callback raised")

	def _fire_mirror_delete(self, cli_session_id: str) -> None:
		self._mirror_canon.pop(cli_session_id, None)
		if self._mirror is None:
			return
		try:
			self._mirror(cli_session_id, None)
		except Exception:
			logging.getLogger(__name__).exception("session mirror callback raised")

	# -- writes -----------------------------------------------------------

	def _ensure(self, cli_session_id: str, source: str) -> SessionRecord:
		rec = self._records.get(cli_session_id)
		if rec is None:
			now = self._now()
			rec = SessionRecord(cli_session_id=cli_session_id, started_at=now, last_event_at=now, source=source)
			self._records[cli_session_id] = rec
		return rec

	def record_session_start(
		self, cli_session_id: str, *, cwd: str, cli: str = "claude", start_source: str | None = None,
	) -> SessionRecord:
		from server.conversation_ops import _infer_surface
		rec = self._ensure(cli_session_id, source="hook")
		now = self._now()
		rec.cwd = cwd or rec.cwd
		rec.surface = _infer_surface(rec.cwd) if rec.cwd else rec.surface
		rec.cli = cli
		rec.started_at = now
		rec.last_event_at = now
		rec.state = "idle"
		rec.state_detail = start_source
		rec.end_reason = None
		self._fire_mirror(rec)
		return rec

	def upsert_from_hook(self, cli_session_id: str, *, state: str, detail: str | None = None) -> SessionRecord:
		rec = self._ensure(cli_session_id, source="hook")
		rec.state = state
		rec.state_detail = detail
		rec.last_event_at = self._now()
		if rec.end_reason is not None:
			rec.end_reason = None
		self._fire_mirror(rec)
		return rec

	def touch_mcp(self, cli_session_id: str, *, cwd: str, sender: str | None = None) -> SessionRecord:
		rec = self._ensure(cli_session_id, source="mcp")
		if cwd:
			from server.conversation_ops import _infer_surface
			rec.cwd = cwd
			rec.surface = _infer_surface(cwd)
		if sender:
			rec.sender = sender
		rec.last_event_at = self._now()
		self._fire_mirror(rec)
		return rec

	def record_session_end(self, cli_session_id: str, *, reason: str, ended_at: str) -> SessionRecord | None:
		rec = self._records.get(cli_session_id)
		if rec is None:
			return None
		rec.state = "ended"
		rec.end_reason = reason
		rec.last_event_at = ended_at or self._now()
		self._fire_mirror(rec)
		return rec

	def apply_rings(self, rings: dict) -> None:
		"""Enrich known sessions from a Watchtower snapshot; discover unknown ones.
		A ring sighting bumps last_event_at (a second, hook-independent liveness
		signal) but never changes state - state is the hooks' story."""
		for session_id, ring in (rings or {}).items():
			if not isinstance(ring, dict):
				continue
			rec = self._records.get(session_id)
			if rec is None:
				rec = self._ensure(session_id, source="rings")
			model = ring.get("model")
			pct = ring.get("pct")
			if isinstance(model, str) and model:
				rec.model = model
			if isinstance(pct, (int, float)):
				rec.context_pct = float(pct)
			rec.last_event_at = self._now()
			self._fire_mirror(rec)

	def set_binding(self, cli_session_id: str, conversation_id: str | None) -> None:
		"""Reflect Registry.bind_session/unbind_session onto the record. Unknown
		ids are ignored: binding is an attribute of a session, not a birth event."""
		rec = self._records.get(cli_session_id)
		if rec is None:
			return
		rec.conversation_id = conversation_id
		self._fire_mirror(rec)

	def set_sender(self, cli_session_id: str, sender: str) -> None:
		"""Record the session's display name. Called from the conversation
		membership paths, which hold the DISAMBIGUATED sender - the roster shows
		exactly what the phone bubbles show. Unknown ids are ignored."""
		rec = self._records.get(cli_session_id)
		if rec is None or not sender:
			return
		rec.sender = sender
		self._fire_mirror(rec)

	def hydrate_record(self, data: dict) -> None:
		"""Rebuild one record from its RTDB payload at startup. Trusts the stored
		state and last_event_at (honest ages); the sweeper judges from there."""
		sid = data.get("cli_session_id")
		if not isinstance(sid, str) or not sid:
			return
		rec = SessionRecord(
			cli_session_id=sid,
			cwd=data.get("cwd") or "",
			surface=data.get("surface") or "windows",
			cli=data.get("cli") or "claude",
			started_at=data.get("started_at") or "",
			last_event_at=data.get("last_event_at") or "",
			state=data.get("state") if data.get("state") in SESSION_STATES else "idle",
			state_detail=data.get("state_detail"),
			conversation_id=data.get("conversation_id"),
			sender=data.get("sender"),
			model=data.get("model"),
			context_pct=data.get("context_pct"),
			end_reason=data.get("end_reason"),
			source="hydration",
		)
		self._records[sid] = rec
		# Seed the canon so hydration does not immediately re-write RTDB with
		# an identical payload.
		payload = rec.to_payload()
		self._mirror_canon[sid] = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

	# -- sweep ------------------------------------------------------------

	def sweep(
		self,
		*,
		now_ts: float,
		lost_after_seconds: float,
		retention_seconds: float,
		rings_fresh: bool,
		ring_ids: set[str],
	) -> list[str]:
		"""Apply staleness rules. Returns the ids pruned (terminal past retention)
		so the caller can delete their RTDB entries. Marks lost only when ALL of:
		silent past lost_after_seconds, not blocked awaiting human/agent, absent
		from a FRESH Watchtower snapshot. When the sensor is stale/offline the
		lost-marking is suspended entirely - never guess from a blind sensor."""
		pruned: list[str] = []
		for sid, rec in list(self._records.items()):
			age = _age_seconds(rec.last_event_at, now_ts)
			if age is None:
				continue
			if rec.state in TERMINAL_STATES:
				if age > retention_seconds:
					del self._records[sid]
					pruned.append(sid)
					self._fire_mirror_delete(sid)
				continue
			if not rings_fresh:
				continue
			if rec.state in SWEEP_EXEMPT_STATES:
				continue
			if sid in ring_ids:
				continue
			if age > lost_after_seconds:
				rec.state = "lost"
				rec.end_reason = "presumed-dead"
				self._fire_mirror(rec)
		return pruned


def _age_seconds(iso: str, now_ts: float) -> float | None:
	if not iso:
		return None
	try:
		return now_ts - datetime.fromisoformat(iso).timestamp()
	except ValueError:
		return None


WATCHTOWER_FRESH_SECONDS = 120


def rings_are_fresh(pushed_at_iso: str | None, now_ts: float) -> bool:
	"""A Watchtower snapshot counts as fresh sensor data only within this window;
	beyond it (or with no push at all) lost-marking is suspended, never guessed."""
	if not pushed_at_iso:
		return False
	age = _age_seconds(pushed_at_iso, now_ts)
	return age is not None and 0 <= age <= WATCHTOWER_FRESH_SECONDS
