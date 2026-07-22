# Design Spec: Session "Needs You" Indicator Dot & Needs-You Session Retention in Watchtower

**Date:** 2026-07-21
**Revised:** 2026-07-22 — design review: corrected the retention mechanism (scanner-level, not aggregator), switched to a render-time join in DetailPanel, replaced the id array with a `needs_you` map, added `blocked_on_approval`, added rollout/security/behavior sections.
**Status:** Revised Proposal (Pending Approval)

## Context & Problem Statement

In Watchtower's detail popup, the indicator dot to the left of each session row shows only transcript liveness: **Green** (`StatusColors.Green`) when the transcript was written within `LiveThresholdSeconds` (default 90s), **Muted grey** otherwise (`DetailPanel.OnPaint`, `ActiveClassifier.StatusFor`).

Switchboard knows when a session is blocked on a human: a pending `ask_human` question (live, or parked after a server restart), or a terminal permission prompt (`SessionRecord.blocked_on_approval`). The phone and Operator surface this; Watchtower — the at-desk ambient surface — does not. The widget/tray already show a **global** amber pending badge (`WidgetWindow.SetPending`, driven by `pending_count > 0`), but nothing attributes the pending to a specific session row.

Two problems:

1. **No per-session attribution.** The popup cannot show *which* session needs attention.
2. **Needs-you sessions vanish.** Sessions disappear from Watchtower after `ActiveWindowMinutes` (default **5**) of transcript silence. The cutoff is applied at **scanner enumeration** (`WindowsSessionScanner.ActiveTranscripts` / `WslSessionScanner.ActiveTranscripts` via `ActiveClassifier.IsActive`) — aged-out transcripts never reach `SessionAggregator` at all. A session sitting on an unanswered question for ten minutes is invisible in the popup. (A separate 60-minute constant, `LastKnownStore.SessionFreshness`, gates only whether the **startup cache** renders; it is not the live pruning rule.)

## Definition: "needs you" (Watchtower)

A session **needs you** when either:

- **`ask`** — it has a pending `ask_human` question in the Registry's pending index. Parked (future-less, post-restart) pendings count: they remain phone-answerable, and they are the durable case — the sweep's `awaiting_human` exemption is conditional on a live future (T-001), so a parked pending's session record can go `lost` and be pruned while the question survives.
- **`approval`** — its `SessionRecord.blocked_on_approval` is true (in a tool with a `star` title-state: a terminal permission prompt). This is the at-desk needs-you par excellence — it cannot be answered from the phone — and both the phone (`SessionBoardPolicy.sessionNeedsAttention`) and Operator (`derive.js needsAttention`) already count it. Watchtower ignoring it would be a cross-surface inconsistency.

Deliberately **excluded**: Operator/phone's `idle AND unacknowledged` clause — that derivation depends on the phone's per-session ack store, which Watchtower has no access to and no need for (the popup already shows liveness directly). `awaiting_agent` is also excluded (waiting on another agent is not waiting on John). The `awaiting_human` session *state* is not used as a source: `ask_human` is the only `CLEAR_TOOLS` entry, so every `awaiting_human` session already has a pending — the pending index is the same signal, more durable.

## Requirements

1. **Indicator dot**: the dot next to a session row in Watchtower's detail popup MUST render `StatusColors.Amber` when that session needs you (definition above). Attention wins over liveness: amber even if the transcript is Live. (Reuses the existing shared amber — same hue as the global pending badge, keeping "amber = pending attention" consistent across widget, tray, and popup. No new color constant.)
2. **Retention**: a needs-you session MUST remain in the popup list regardless of transcript age (bypassing the `ActiveWindowMinutes` scan window and the startup-cache freshness gate).
3. **Ordering**: needs-you sessions sort to the top of the popup list (stable — existing busiest-first order preserved within each group), mirroring the phone board's needs-attention-first partition.
4. **Freshness**: the dot appears/clears on the `/stats` poll cadence (`Switchboard.PollSeconds`, default 4s), not the 60s session-scan cadence.
5. **Degradation**: when `/stats` is unreachable, Watchtower holds the last successfully fetched needs-you set (never clears dots on fetch failure), consistent with the last-known render philosophy (T-162). At startup the set seeds from `last-known.json`.

## Architectural Design

```text
+----------------------------------------------------------------------+
| Switchboard Server (Python)                                          |
|                                                                      |
|  GET /stats  (additive field; existing payload unchanged)            |
|    "needs_you": {                                                    |
|      "<cli_session_id>": {"reason": "ask"|"approval",                |
|                            "age_seconds": <float>},  ...             |
|    }                                                                 |
|    sources: registry.all_pending()  (live + parked)  -> "ask"        |
|             session_registry records with blocked_on_approval        |
|             (non-terminal)                            -> "approval"  |
+-----------------------------------+----------------------------------+
                                    | HTTP GET /stats (existing 4s poll)
                                    v
+-----------------------------------+----------------------------------+
| Watchtower (C#)                                                      |
|                                                                      |
|  SwitchboardStats.Parse   +NeedsYou map (absent field -> empty)      |
|  AppHost                  passes _lastSwitchboardStats.NeedsYou keys |
|                           into both scanners at each scan tick       |
|  Windows/WslSessionScanner  yield transcript when mtime-active OR    |
|                             stem in retainIds        (retention)     |
|  DetailPanel              render-time join: dot amber when           |
|                           SessionId in held needs-you map; sort      |
|                           needs-you rows first                       |
|  LastKnownStats           +NeedsYou (round-trip; startup seed +      |
|                           stale-cache render exemption)              |
|                                                                      |
|  SessionModel / SessionAggregator / LastKnownSession: UNCHANGED      |
+----------------------------------------------------------------------+
```

The join key is `SessionModel.SessionId` (transcript filename stem) == `cli_session_id` — the established correlation (ring == transcript stem == member cli_session_id, 2026-06-25 design).

### 1. Server — `/stats` (`server/main.py`, `_build_stats_route`)

Add `needs_you` to the existing payload (all current fields, including the `"sessions"` roll-up block, unchanged):

```python
from server.session_registry import TERMINAL_STATES

now = datetime.now(timezone.utc)
needs_you: dict[str, dict] = {}
for p in registry.all_pending():  # includes parked (future-less) pendings
	age = (now - p.started_at).total_seconds()
	prev = needs_you.get(p.cli_session_id)
	if prev is None or age > prev["age_seconds"]:  # session in >1 conversation: keep oldest
		needs_you[p.cli_session_id] = {"reason": "ask", "age_seconds": age}
if session_registry is not None:
	for rec in session_registry.snapshot():  # snapshot() returns a list
		if rec.blocked_on_approval and rec.state not in TERMINAL_STATES and rec.cli_session_id not in needs_you:
			needs_you[rec.cli_session_id] = {"reason": "approval", "age_seconds": <seconds since rec.last_event_at, 0.0 if unparsable>}
```

Decisions:

- **`ask` takes precedence** over `approval` when both apply — the phone-answerable fact is the more actionable one.
- **`age_seconds`** is `now - PendingRequest.started_at` for `ask`; for `approval` there is no block-start stamp, so seconds since `last_event_at` is the proxy.
- **Terminal-state records are excluded** from the `approval` source as a belt (end/lost transitions already clear `in_tool` and recompute the flag).
- **Map, not array**: `{sid: {reason, age_seconds}}` costs the same to produce, gives the UI the reason distinction and a per-session age for a tooltip, and leaves room for future reasons without another schema change. Unknown reason strings must still light the dot on the client (forward compatibility).

**Security note (REV-003 consistency).** This is the first server-to-client exposure of full `cli_session_id`s over HTTP; `/sessions` deliberately redacts them to 8-char prefixes because the full id is the forgeable routing identity. Exposing them on `/stats` is acceptable because (a) `/stats` is loopback-trusted and token-gated for non-loopback callers exactly like every other route, (b) any local process can already read every session id directly from transcript filenames under `~/.claude/projects`, and (c) Watchtower already sends full ids to the server in ring snapshots — this is the same trust boundary in the other direction. `/sessions` keeps its redaction.

### 2. Watchtower Core (`watchtower/src/Switchboard.Watchtower.Core/`)

#### `SwitchboardStats.cs`

- New record: `public sealed record NeedsYouEntry(string Reason, double AgeSeconds);`
- `SwitchboardStats` gains `IReadOnlyDictionary<string, NeedsYouEntry> NeedsYou`.
- Parse rules: field **absent → empty map** (rollout tolerance: a new Watchtower against a not-yet-restarted server must not null the whole stats parse and grey out the Switchboard block); field **present but malformed → null** (consistent with the parser's strict philosophy: absence is a version signal, malformation is a bug); **unknown `reason` values are accepted** (dot lights amber; only tooltip text differentiates).

#### `WindowsSessionScanner.cs` / `WslSessionScanner.cs`

- `ActiveTranscripts(...)` gains `IReadOnlySet<string>? retainIds = null`.
- Yield a transcript when `ActiveClassifier.IsActive(mtime, ...)` **or** `retainIds.Contains(stem)` (stem = `Path.GetFileNameWithoutExtension`). All files are already enumerated and mtime-filtered in memory, so the bypass adds no IO.
- This is the retention mechanism. `SessionAggregator` has no age logic and is unchanged; a retained old transcript flows through `UsageReader.Read` normally and simply arrives with `Status == Idle` (the amber dot, not the status, carries the attention signal).

#### `LastKnown.cs`

- `LastKnownStats` gains `Dictionary<string, LastKnownNeedsYou> NeedsYou` (`Reason` + `AgeSeconds`); round-tripped by `From(...)` / `ToStats(...)`. Missing on an old cache deserializes to empty — no `Version` bump needed.
- Stale-cache exemption: today `RenderLastKnown` renders cached sessions all-or-nothing behind `SessionsFresh` (60 min). Change: when the cache is stale, render the subset of cached sessions whose `SessionId` is in the cached `NeedsYou` map instead of nothing. Implement the filter as a pure `LastKnownStore` helper so it is unit-testable.
- `LastKnownSession` is **unchanged** (no per-session flag is persisted; the map on the stats side is the single source).

### 3. Watchtower host + UI (`watchtower/src/Switchboard.Watchtower/`)

#### `AppHost.cs`

- The scan tick passes `_lastSwitchboardStats?.NeedsYou.Keys` (as a set) into both scanner calls. `_lastSwitchboardStats` already holds the last **successful** fetch (failed polls keep it), which gives requirement 5's hold-on-failure for retention for free.
- Startup: seed `_lastSwitchboardStats` from the last-known cache (today the cached stats go only to the panel), so retention and dots survive a Watchtower start while the server is down.

#### `DetailPanel.cs`

- Holds a needs-you map updated from `UpdateSwitchboard(...)` **only when `stats` is non-null** — a null (unreachable) poll leaves the held map untouched, so dots never clear on failure while the stats line correctly shows unreachable.
- `OnPaint` dot (render-time join — no `SessionModel` change; dot freshness rides the 4s stats poll instead of the 60s scan):

```csharp
bool needsYou = s.SessionId is not null && _needsYou.ContainsKey(s.SessionId);
Color dotColor = needsYou
	? StatusColors.Amber
	: (s.Status == SessionStatus.Live ? StatusColors.Green : _palette.Muted);
```

- Row ordering: needs-you rows first, stable within groups (the aggregator's busiest-first order is preserved). Recomputed on both `UpdateSessions` and `UpdateSwitchboard` (a dot arriving between scans must also re-sort).
- Optional nicety: row tooltip via the existing `ToolTip` — "awaiting your reply (8m)" / "blocked on approval (3m)" from `NeedsYouEntry` (`RelativeTime` for formatting). May be dropped from scope without affecting the requirements.

## Behavior Summary

| Event | Surface change | Latency |
| --- | --- | --- |
| `ask_human` fires | dot turns amber, row sorts up | ≤ ~4s (stats poll) |
| Question answered / cancelled | dot reverts to Live/Idle color | ≤ ~4s |
| Needs-you transcript ages past `ActiveWindowMinutes` | row retained (scanner bypass) | next scan tick, ≤ 60s |
| Answered + aged out | row disappears | next scan tick after the poll |
| Server restart with pending ask | pending parks; still `ask` in `needs_you` | unchanged |
| `/stats` unreachable | dots + retention hold last-good set; stats line shows unreachable | until next successful poll |
| Watchtower restart, server down | needs-you subset renders from stale cache | startup |

## Scope Limitations (stated, accepted)

- **Transcript-visible sessions only.** Watchtower renders sessions it can scan: Windows `~/.claude/projects` plus WSL distros when `ScanWsl` is on. A pending Antigravity session, or a WSL pending with scanning off, gets no row and therefore no dot — the retention rule cannot conjure rows for invisible sessions. The global amber pending badge remains the cue for those.
- **Widget bar unchanged.** Per-session attribution lives in the popup; the taskbar widget keeps its existing global badge. No ring tinting.
- **No new pruning semantics server-side.** The sweep, retention horizons, and pending lifecycle are untouched; this feature is read-only over existing state.

## Considered Alternative: polling `GET /sessions` (rejected for now)

`/sessions` already returns full per-session records (state, `blocked_on_approval`, sender, conversation binding). Rejected because: (1) it deliberately redacts `cli_session_id` to an 8-char prefix (REV-003 — the route's consumer is "a human debugging"), which destroys the transcript-stem join; using it means reversing a recorded security decision or prefix-matching a redacted field. (2) The pending index is not in the payload and cannot be derived from session state — parked pendings can outlive their (lost, pruned) session records, so `/sessions` alone misses the most durable needs-you case; fixing that grows a different endpoint by the same amount with a worse fit. (3) It serializes the internal `SessionRecord` shape, coupling the C# parser to server internals that evolve freely, whereas `/stats` is the deliberately curated widget contract Watchtower already polls, caches, and round-trips. (4) One payload keeps the badge and the dots atomically consistent.

**Flip condition:** if the popup ever grows toward a real sessions board (state chips, sender names, conversation bindings, Operator-parity needs-attention), switch the poll target to `/sessions` once, revisit the REV-003 redaction deliberately (e.g. an authenticated or full-id variant), and derive per-session semantics the way Operator does.

## Rollout

Server first, then Watchtower — the tolerant parse (absent → empty) covers the window where a new Watchtower talks to an old server, and an old Watchtower simply ignores the new field. Server restart via `.\scripts\restart-service.ps1 -SkipTests`; Watchtower rebuild + relaunch (note the live widget process holds `publish/` — quit the tray app before publishing). No plugin bump (no hook/skill changes).

## Verification & Testing Plan

### Unit tests

1. **Server — `tests/test_stats_endpoint.py`**: `needs_you` contains a live pending (`reason == "ask"`, plausible age from `started_at`); contains a parked (future-less) pending; contains a `blocked_on_approval` record (`reason == "approval"`); excludes terminal-state approval records; `ask` wins when both sources match one session; oldest age wins for a session pending in two conversations; empty map when nothing is pending; existing payload fields (including the `sessions` block) unchanged.
2. **`SwitchboardStatsTests.cs`**: absent `needs_you` → parse succeeds with empty map (old-server tolerance); present-but-malformed → whole parse null; well-formed map parses; unknown reason string accepted.
3. **`WindowsSessionScannerTests.cs` / `WslSessionScannerTests.cs`**: an old-mtime transcript is excluded normally but yielded when its stem is in `retainIds`; `retainIds: null` preserves existing behavior exactly.
4. **`LastKnownTests.cs`**: `NeedsYou` round-trips through save/load; a pre-existing cache file without the field loads with an empty map; the stale-cache filter helper returns the needs-you subset when `SessionsFresh` is false and everything when fresh.

### Manual E2E

1. Trigger `ask_human` from a test session (away mode on) → popup dot turns **amber within ~4s** and the row sorts to the top.
2. Age the session's transcript **mtime** past `ActiveWindowMinutes` (e.g. `(Get-Item <transcript>).LastWriteTimeUtc = (Get-Date).AddMinutes(-30)`) → after the next scan tick the row is still listed. (The prior plan's "simulate `LastActiveUtc`" tested the wrong layer — the cutoff is scanner-side on file mtime.)
3. Answer from the phone → dot reverts within ~4s; after `ActiveWindowMinutes` the row ages out normally.
4. Restart the server mid-ask (pending parks) → dot stays amber (parked pendings included).
5. Block a session on a terminal permission prompt (star title) → dot amber with `approval` reason (tooltip, if implemented).
6. Stop the server → dots and retained rows persist on last-good data; stats line shows unreachable. Relaunch Watchtower while the server is still down → needs-you rows render from the stale cache.

## As-Built (2026-07-22)

Implemented exactly as designed across the three layers, via a no-commit subagent-driven run (6 tasks; sonnet implementers, opus reviews). Server suite 948 → 955 (+7 `needs_you` tests); Watchtower Core 176 → 188 (+12); UI built clean (0 errors / 0 warnings). Each task passed spec + code review; the final whole-feature review returned "Ready to merge" with no Critical/Important, having verified the cross-layer seams — most importantly that the join key `cli_session_id == SessionModel.SessionId == transcript filename stem` is a proven single identity (set by `UsageReader`/`SessionAggregator` and already round-tripped back to the server as the ring id by `WidgetSnapshot`).

Deployed and live-verified (John-guided): the server restarted and `GET /stats` now carries `needs_you` alongside the pre-existing fields; the Watchtower widget was rebuilt and relaunched. Core live gate PASSED — a real `ask_human` from a live session turned that session's popup row dot amber and sorted it to the top within the ~4s poll cadence (requirement 1, and the join key + freshness end to end), confirmed by direct visual inspection; on answering, `/stats needs_you` emptied to `{}` and the row cleared (requirement 3, confirmed server-side and by the proven poll-reflects-`/stats` behavior). The remaining spec checks (retention past the active window, parked-pending survival across restart, the `approval` reason, and server-down stale-cache render) were left to ad-hoc verification rather than run in this session; the reviewed code paths and the live server-side confirmation cover them.

The optional per-row tooltip was deferred (DetailPanel has no per-row hit-testing), as the spec permits. A one-shot `watchtower/deploy-widget.ps1` (stop → publish → relaunch) was added to make widget redeploys repeatable.
