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
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Callable

from server.clock import now_iso

SESSION_STATES = ("active", "idle", "awaiting_human", "awaiting_agent", "ended", "lost")
TERMINAL_STATES = ("ended", "lost")
# awaiting_* sessions may be legitimately silent for hours - but only while an
# actual in-memory blocking structure exists (a future-bearing pending for
# awaiting_human, a live wait-queue entry for awaiting_agent). The exemption is
# conditional (T-001): a hydrated awaiting_* record with no live structure is
# lost-markable on the normal silence threshold.
SWEEP_EXEMPT_STATES = ("awaiting_human", "awaiting_agent")

# Marker-health detector: warn once when this many sessions have been
# presumed dead while zero SessionEnd markers were applied since startup -
# the signature of a client host whose marker dir is never swept (env var
# unset, hook falling back to the plugin cache).
MARKER_HEALTH_PRESUMED_DEAD_THRESHOLD = 3


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
	name: str | None = None
	name_source: str | None = None
	last_transition_source: str | None = None
	title_state: str | None = None
	in_tool: bool = False
	blocked_on_approval: bool = False
	pending_notices: list = field(default_factory=list)

	def to_payload(self) -> dict:
		return asdict(self)


class SessionRegistry:
	def __init__(self, now: Callable[[], str] = now_iso, mono: Callable[[], float] | None = None) -> None:
		self._records: dict[str, SessionRecord] = {}
		self._now = now
		self._mono = mono or time.monotonic
		self._mirror: Callable[[str, dict | None], None] | None = None
		self._mirror_canon: dict[str, str] = {}
		self._recent_resumes: list = []
		self.markers_applied_total: int = 0
		self.presumed_dead_total: int = 0
		self._marker_health_warned: bool = False

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
		rec.last_transition_source = "session_start"
		self._fire_mirror(rec)
		return rec

	def upsert_from_hook(
		self, cli_session_id: str, *, state: str, detail: str | None = None,
		cwd: str | None = None, event: str | None = None, in_tool: bool | None = None,
		cli: str | None = None,
	) -> SessionRecord:
		rec = self._ensure(cli_session_id, source="hook")
		if rec.state == "ended":
			# REV-114: an explicit SessionEnd is authoritative. A straggler
			# hook POST (Stop hook racing the end-marker sweep at session exit)
			# must not resurrect the record - that would hide the phone Resume
			# affordance until the sweeper falsely re-marks the session lost.
			# A genuine new life for this id arrives as a SessionStart
			# (record_session_start), which still resets state. "lost" is
			# deliberately not guarded: it is the sweeper's presumption, and a
			# live hook event is proof of life.
			return rec
		if cli:
			rec.cli = cli
		if cwd and not rec.cwd:
			from server.conversation_ops import _infer_surface
			rec.cwd = cwd
			rec.surface = _infer_surface(cwd)
		if rec.state != state and event:
			rec.last_transition_source = f"hook:{event}"
		rec.state = state
		rec.state_detail = detail
		rec.last_event_at = self._now()
		if rec.end_reason is not None:
			rec.end_reason = None
		if in_tool is not None:
			rec.in_tool = in_tool
		self._recompute_blocked(rec)
		self._fire_mirror(rec)
		return rec

	def _recompute_blocked(self, rec: SessionRecord) -> None:
		rec.blocked_on_approval = bool(rec.in_tool and rec.title_state == "star")

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
		rec.last_transition_source = "session_end"
		rec.in_tool = False
		self._recompute_blocked(rec)
		self._fire_mirror(rec)
		return rec

	def apply_rings(self, rings: dict) -> None:
		"""Enrich known sessions from a Watchtower snapshot; discover unknown ones.
		A ring sighting bumps last_event_at (a second, hook-independent liveness
		signal) but never changes state - state is the hooks' story. Terminal
		(ended/lost) records are skipped entirely: a transcript ring is not
		proof of life and must not defer retention pruning (REV-114)."""
		for session_id, ring in (rings or {}).items():
			if not isinstance(ring, dict):
				continue
			rec = self._records.get(session_id)
			if rec is None:
				rec = self._ensure(session_id, source="rings")
			elif rec.state in TERMINAL_STATES:
				# REV-114: a ring is a transcript-file sighting, not proof of
				# life - transcripts outlive their sessions. Bumping
				# last_event_at here would defer the retention prune for as
				# long as Watchtower keeps sighting the file. Skip terminal
				# records entirely (no enrichment, no mirror churn).
				continue
			model = ring.get("model")
			pct = ring.get("pct")
			if isinstance(model, str) and model:
				rec.model = model
			if isinstance(pct, (int, float)):
				rec.context_pct = float(pct)
			name = ring.get("name")
			name_source = ring.get("name_source")
			if isinstance(name, str) and name:
				rec.name = name
				rec.name_source = name_source if isinstance(name_source, str) else None
			if "title_state" in ring:
				ts = ring.get("title_state")
				rec.title_state = ts if isinstance(ts, str) and ts in ("working", "star") else None
			self._recompute_blocked(rec)
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

	def queue_notice(self, cli_session_id: str, text: str) -> bool:
		"""Queue a wake notice for hook delivery (turn-end block reason or
		UserPromptSubmit context). At-most-once: pop_notices clears on read; the
		convene intro message in the conversation is the backstop for a lost one."""
		rec = self._records.get(cli_session_id)
		if rec is None or not text:
			return False
		rec.pending_notices.append(text)
		self._fire_mirror(rec)
		return True

	def pop_notices(self, cli_session_id: str) -> list[str]:
		rec = self._records.get(cli_session_id)
		if rec is None or not rec.pending_notices:
			return []
		notices = list(rec.pending_notices)
		rec.pending_notices.clear()
		self._fire_mirror(rec)
		return notices

	def mark_wait_cancelled(self, cli_session_id: str) -> None:
		"""John cancelled the blocking tool call from the CLI; without this the
		roster shows a stale awaiting_* chip until the next hook event."""
		rec = self._records.get(cli_session_id)
		if rec is None:
			return
		rec.state = "active"
		rec.state_detail = "wait-cancelled"
		rec.last_transition_source = "cancel"
		rec.last_event_at = self._now()
		self._fire_mirror(rec)

	def note_spawn_resume(self, cli_session_id: str, cwd: str) -> None:
		"""Sentinel bookkeeping: remember spawn-driven resumes so /session_start can
		detect if CC ever stops preserving session ids across --resume (verified
		preserved 2026-07-07; this is the tripwire, not a mechanism)."""
		self._recent_resumes.append({"session_id": cli_session_id, "cwd": cwd, "at": self._mono()})
		if len(self._recent_resumes) > 32:
			del self._recent_resumes[0]

	def check_resume_id_change(self, new_session_id: str, cwd: str) -> str | None:
		now = self._mono()
		self._recent_resumes = [e for e in self._recent_resumes if now - e["at"] <= 180.0]
		if new_session_id in self._records:
			return None
		for i, entry in enumerate(self._recent_resumes):
			if entry["cwd"] == cwd and entry["session_id"] != new_session_id:
				del self._recent_resumes[i]
				return entry["session_id"]
		return None

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
			name=data.get("name"),
			name_source=data.get("name_source"),
			last_transition_source=data.get("last_transition_source"),
			title_state=data.get("title_state"),
			in_tool=bool(data.get("in_tool", False)),
			blocked_on_approval=bool(data.get("blocked_on_approval", False)),
			pending_notices=list(data.get("pending_notices") or []),
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
		live_ask_ids: set | None = None,
		live_wait_ids: set | None = None,
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
			if rec.state in SWEEP_EXEMPT_STATES and _exemption_live(rec.state, sid, live_ask_ids, live_wait_ids):
				continue
			if sid in ring_ids:
				continue
			if age > lost_after_seconds:
				rec.state = "lost"
				rec.end_reason = "presumed-dead"
				self.presumed_dead_total += 1
				rec.in_tool = False
				self._recompute_blocked(rec)
				self._fire_mirror(rec)
		return pruned

	def marker_health_check(self) -> bool:
		"""True exactly once, when presumed-dead transitions reach the threshold
		while zero SessionEnd markers have been applied this process. The caller
		owns the warning side effect; this method owns the trigger + one-shot."""
		if self._marker_health_warned:
			return False
		if self.markers_applied_total == 0 and self.presumed_dead_total >= MARKER_HEALTH_PRESUMED_DEAD_THRESHOLD:
			self._marker_health_warned = True
			return True
		return False


def _age_seconds(iso: str, now_ts: float) -> float | None:
	if not iso:
		return None
	try:
		return now_ts - datetime.fromisoformat(iso).timestamp()
	except ValueError:
		return None


def _exemption_live(state: str, sid: str, live_ask_ids: set | None, live_wait_ids: set | None) -> bool:
	"""Blanket exemption when the caller supplied no liveness info (legacy
	callers, tests); otherwise exempt only a state backed by its live structure."""
	if live_ask_ids is None and live_wait_ids is None:
		return True
	if state == "awaiting_human":
		return sid in (live_ask_ids or set())
	return sid in (live_wait_ids or set())


WATCHTOWER_FRESH_SECONDS = 120


def rings_are_fresh(pushed_at_iso: str | None, now_ts: float) -> bool:
	"""A Watchtower snapshot counts as fresh sensor data only within this window;
	beyond it (or with no push at all) lost-marking is suspended, never guessed."""
	if not pushed_at_iso:
		return False
	age = _age_seconds(pushed_at_iso, now_ts)
	return age is not None and 0 <= age <= WATCHTOWER_FRESH_SECONDS
