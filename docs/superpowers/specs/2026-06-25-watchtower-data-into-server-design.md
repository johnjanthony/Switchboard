# Watchtower data into the server: shared rings / quota / status hub - design

**Date:** 2026-06-25
**Status:** Designed (not yet implemented)
**Scope:** Python server (new ingest endpoint, snapshot store, Firebase fan-out, server-side Claude-status watch-loop), Watchtower (.NET; snapshot push + status redirect), Operator (dashboard rendering), Android phone (Phase 2 rendering). No change to how Watchtower *scans* transcripts or reads quota.

## Problem

Watchtower already polls three at-a-glance signals for Claude work on this machine: per-session context-window fill (the "rings"), plan quota (5h / 7d), and Anthropic service status (T-179). Today those signals live only on the taskbar widget. They are most valuable exactly when John is away from the desk - "is an agent about to run out of context?", "am I near a plan wall?", "is it me or Anthropic?" - but the phone cannot see any of them, and Operator cannot either.

The goal is to make the server a shared hub for this data so it can be rendered on the phone and in Operator, with Watchtower, Operator, and the phone all reflecting one consistent source.

## Decisions taken during brainstorming

- **Driver:** full centralization - one source of truth plus new phone visibility - not merely de-duplication.
- **The server cannot be the collector. Verified, not assumed.** A temporary read-only probe was run inside the actual running service (faithful Session 0 condition) and revealed two things:
  - The running service executes as **LocalSystem** (`username` reported as the machine account, `userprofile` = `C:\Windows\system32\config\systemprofile`), despite `scripts/install-service.ps1` *intending* to run it as the interactive user. This means the server's `~`/`%USERPROFILE%` is the system profile, so it cannot read John's `~/.claude/projects` (Windows transcripts) or `~/.claude/.credentials.json` (quota) by the usual path.
  - As LocalSystem in Session 0, `\\wsl.localhost\...` is unreachable (`WinError 67 "The network name cannot be found"`, a fast fail, not a hang) - WSL distros are a per-user VM, and LocalSystem has no instance. SYSTEM privilege does not conjure John's VM.
  - Consequence: collection of all identity-bound data (Windows rings, WSL rings, quota) must stay in a process that runs as John in his interactive session. Watchtower already is that process. The LocalSystem-vs-install-script discrepancy is noted as a separate observation; this design does not depend on resolving it, and running the service as John would not rescue WSL anyway (Session-0-as-John WSL access is unproven and the per-user VM concern remains).
- **The reframe: Watchtower is the sole sensor; the server is the hub; Operator and the phone are readers.** Watchtower keeps polling rings and quota exactly as it does today and additionally pushes a snapshot to the server. No scan/quota logic is ported to Python.
- **Push via the server, not directly to Firebase.** Watchtower's only outbound dependency stays localhost HTTP to the server (as the existing hooks already do). The server is already the sole Firebase write-authority and owns the away-mode gate, the RTDB schema, and the command-dispatch loops; routing through it avoids putting a Firebase SDK and a service-account credential into the .NET app and avoids a second Firebase writer.
- **Rings are speaker annotations, always fanned out.** Rings are not a standalone phone screen. Each ring is tagged with its Claude Code `session_id`; consumers attach the context-% to the matching conversation member. This reverses the earlier "rings only in away mode" decision: because rings now annotate conversation speakers that John views at any time, the server **always** fans out the rings map (no away-gate). Watchtower keeps its own full rings widget at-desk, unchanged.
- **Status: the server is the only poller, and it owns the watch-loop.** The server is the single thing that ever calls `status.claude.com`, and it does so only in response to a request (from Watchtower over HTTP, or from the phone via an RTDB command). T-179's `ClaudeStatusWatch` state machine is ported into the server, so a request starts a poll-until-resolved loop that keeps the published status current; Watchtower and the phone can fire-and-watch an incident resolve. Watchtower's T-179 watch/dot UI stays but renders the server's published state and sends commands instead of fetching directly.
- **Single host.** Only one machine runs Claude sessions for John. The RTDB schema is not namespaced by host, but leaves room to nest under `widget/{host}/` later.
- **Operator first.** Operator is a zero-build web app testable by a browser refresh, so the data pipeline and rendering are proven in Operator before the Android UI is built.

## The correlation (verified)

The injector hook sets `cli_session_id = payload["session_id"]` (`scripts/cli-session-injector-hook.py`), and Claude Code's `session_id` is the transcript filename (`~/.claude/projects/<encoded-cwd>/<session_id>.jsonl`). Switchboard conversation members store that same `cli_session_id` (`server/conversation_ops.py`; it is also the id used for `claude --resume`). Therefore a Watchtower ring (which knows its transcript's `<session_id>` stem) maps exactly to a conversation member by `member.cli_session_id == ring.session_id`. This holds for WSL sessions too: the WSL-side hook injects the WSL session's id, and Watchtower scans that transcript from the interactive session where WSL is reachable.

## Architecture and data flow

```
Watchtower (Session 1, as John)            Server (LocalSystem, always-on)              Readers
  scan rings (Win + WSL)  ─┐
  poll quota               ├── POST /widget-snapshot ──► store snapshot (+ pushed_at)
  (status: delegated)      ┘                            │
                                                        ├─► RTDB widget/rings  (always, on-change)
                                                        ├─► RTDB widget/quota  (on-change)
                                                        │
  status actions ──── HTTP request ──►  status watch-loop (poll status.claude.com
  phone ──── RTDB status_request ──►     only while watching) ─► RTDB widget/status
                                                        │
                                                        └─► HTTP roll-up (Operator, optional)

Operator (browser, authed RTDB)  ─ reads widget/* ─► context-% on speakers, quota, status + controls
Phone (Android, RTDB)            ─ reads widget/* ─► context-% on speakers, quota, status + controls
```

- The server stores the latest snapshot in memory, stamped with `pushed_at`, and writes RTDB **on-change only** (diff against last-published) to keep phone traffic and battery low.
- Rings are written whenever they change, with no away-gate. Quota is kept current. Status is written by the watch-loop.
- Staleness: if Watchtower stops pushing, `pushed_at` ages; readers show "Watchtower offline / as of N min ago" rather than presenting stale data as live.

## Components

### Server

1. **`POST /widget-snapshot` ingest.** Localhost-trust, same model as `/stats` and `/agent_status`. Body: `{ rings: [ {session_id, pct, model, status, context_tokens, window, is_error} ], quota: {session:{pct,resets_at}, weekly:{pct,resets_at}, polled_at} | null, pushed_at }`. Validates shape, updates the in-memory snapshot, triggers on-change RTDB writes. Returns 200 with a small ack.

2. **Snapshot store.** In-memory holder for the latest rings list, quota, and the derived staleness timestamp. Not persisted (it is live telemetry; a restart simply waits for the next push). Lives alongside the existing in-memory Registry, not in it.

3. **Firebase fan-out.** On each ingest, diff against the last-published values and write only changed nodes under `widget/` (see schema). Reuses the existing Firebase write path.

4. **Claude-status watch-loop.** A port of T-179's `ClaudeStatusWatch` (pure state machine: Idle / Watching / ResolvedUnacked / CappedUnacked) plus a fetch (`summary.json` parse, ~15 lines) and a background poll task (interval = `WatchIntervalSeconds`, capped by `MaxWatchMinutes`). Triggered by a request:
   - **Watchtower** posts status commands over HTTP (check / stop / acknowledge).
   - **Phone and Operator** write to `widget/status_request` (an RTDB command queue, dispatched like the existing `*_commands`).
   The loop publishes `widget/status` (level, description, incidents, fetched_at, watch state) so all readers see the same status and can fire-and-watch.

### Watchtower (.NET)

- **Rings:** tag each `SessionModel` push with its `session_id` (the transcript filename stem, already available during the scan). Its own widget display is unchanged.
- **Snapshot push:** a small HTTP client posts `{rings, quota, pushed_at}` to `/widget-snapshot` on the existing scan/quota cadence (next to `SwitchboardStatsReader`). New config: the snapshot-push URL, alongside the existing `StatsUrl`.
- **Status redirect:** `ClaudeStatusReader`'s direct `status.claude.com` call is removed; the watch-state machine moves server-side. Watchtower's "Check now / Stop watching / Clear" actions send commands to the server, and its dot/popup render the server's published status. (T-179's Watchtower-side state machine is retired in favor of the server's.)

### Operator (Phase 1) and Phone (Phase 2)

Both render the same three things from their Firebase reads:
- **Context-%** inline on each conversation member whose `cli_session_id` matches an active `widget/rings/{session_id}`, using the existing severity palette (green to amber to red by fill). Members with no active ring show nothing.
- **Quota** panel: 5h and 7d usage with reset countdowns.
- **Status** indicator plus **Check / Stop / Clear** controls (Operator and the phone write `widget/status_request`; Watchtower uses the HTTP command surface); the indicator reflects the server's watch state.

Operator reads `widget/*` from RTDB directly (it already has authed RTDB access via Google sign-in). The phone adds RTDB listeners on `widget/*` and writes `widget/status_request`. Path builders are added to the dashboard's `schema.js` (the single source of path truth) and mirrored on the Android side.

## RTDB schema

```
widget/
  rings/                         # always written, on-change; keyed by Claude Code session_id
    {session_id}: { pct, model, status: "live"|"idle", context_tokens, window, is_error }
  quota/                         # kept current from Watchtower pushes
    session: { pct, resets_at }  # 5h window
    weekly:  { pct, resets_at }  # 7d window
    polled_at
  status/                        # written by the server's watch-loop
    level: "operational"|"minor"|"major"|"critical"|"unknown"
    description
    incidents: [ ... ]
    fetched_at
    watch_state: "idle"|"watching"|"resolved_unacked"|"capped_unacked"
  status_request/                # phone -> server command queue (check / stop / acknowledge)
  pushed_at                      # last Watchtower snapshot time (staleness signal)
```

## Phasing

- **Phase 1 (at-desk, browser/Watchtower-testable, no Android):**
  1. Server: `/widget-snapshot` ingest, in-memory store, staleness, on-change `widget/rings` and `widget/quota` writes.
  2. Server: Claude-status watch-loop (ported), HTTP command surface + `widget/status_request` dispatch, `widget/status` publish.
  3. Watchtower: session_id tagging, snapshot push, status redirect.
  4. Operator: schema paths, context-% on conversation members, quota panel, status panel + controls.
  End-to-end verifiable in a browser and on the widget.
- **Phase 2 (phone):**
  5. Android: context-% annotations on members, quota view, status view + command, RTDB listeners.

Each phase gets its own implementation plan.

## Testing

- **Server (pytest):** ingest validation (well-formed and malformed bodies); on-change diffing (no write when unchanged, write when changed); status watch-loop transitions (port T-179's transition table - Idle to Watching on a degraded check, Watching to ResolvedUnacked on operational, Unknown does not stop the loop, the MaxWatchMinutes cap, acknowledge to Idle); status_request dispatch.
- **Live check:** verify the status fetch against the real `summary.json` at least once (read-only, idempotent, non-billing) so the parser is checked against the real response shape, not only a fixture.
- **Operator (node --test):** the rings-to-member join (match, no-match, dormant member), quota and status derivations, and any new `schema.js` path builders.
- **Watchtower (xUnit):** the snapshot/session_id shape; the status redirect (manual verification for the HTTP glue, per the existing convention).

## Config

- **Server:** a `ClaudeStatus` config block mirroring T-179's defaults - `SummaryUrl` (default `https://status.claude.com/api/v2/summary.json`), `WatchIntervalSeconds` (default 30, floored), `MaxWatchMinutes` (default 180).
- **Watchtower:** the snapshot-push URL, alongside the existing `Switchboard.StatsUrl`.

## Error handling

- Ingest failures degrade quietly: a malformed push is rejected with a 4xx and logged; the last good snapshot stays published.
- The status fetch never throws to the loop: any failure collapses to `level == "unknown"`, and an Unknown result never stops a running watch (only a confirmed Operational does), so a transient blip during an outage does not declare "resolved."
- Firebase write failures are logged and retried on the next change; they never crash the ingest path.

## Out of scope (YAGNI)

- Multi-host namespacing (single host; the schema leaves room for `widget/{host}/`).
- Changing how Watchtower scans transcripts or reads quota (unchanged; only a session_id tag and a push are added).
- A standalone rings screen on the phone (rings are annotations only).
- Resolving the LocalSystem-vs-install-script service-account discrepancy (noted, separate; this design does not require the service to run as John).
- Phone-triggered fresh quota re-poll (the phone reads the latest pushed quota; a phone to server to Watchtower re-poll trigger is a possible later addition).

## Known dependency

Rings and quota freshness track Watchtower being up; it is effectively always-on (autostart, taskbar), and the server stamps `pushed_at` so every reader can show staleness rather than stale-as-live.
