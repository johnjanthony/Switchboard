# Watchtower Daily Session Anchor — Design

- **Date:** 2026-07-22
- **Status:** Approved (brainstorming complete; implementation plan pending)
- **Component:** Watchtower (`watchtower/`) — WinForms widget + Core library. No server-side or Android change.
- **Supersedes behavior in:** `QuotaService` reactive token-refresh probe (introduced by commit 7951d51, "Isolate Watchtower token-refresh probe from user Claude config").

## Problem

Watchtower keeps its plan-usage display (5h session / 7d weekly) alive by reading the OAuth token from `~/.claude/.credentials.json` and calling `GET /api/oauth/usage`. When the token is expired or rejected, it forces a refresh by spawning a headless `claude -p .` turn.

That headless turn is a real, billable model turn, not a bare OAuth call. A billable turn made while no 5-hour usage window is open **starts** a new window at that moment. Because the refresh fires reactively — whenever the access token happens to expire and a poll catches it — the window can be anchored at an arbitrary early-morning time (e.g. 5am) that the operator did not choose. Every subsequent same-day window then rolls from that accidental anchor (10am, 3pm, 8pm) instead of the operator's preferred rhythm.

The operator wants the first session window of the day to start at a time they choose (7am), so the day's windows roll on their schedule (noon, 5pm, 10pm), even when they are not yet at the desk.

## Confirmed facts (live-verified 2026-07-22)

These were established empirically before the design was settled:

1. **A `claude -p .` turn starts/shifts the 5-hour window.** Confirmed by the operator from direct observation.
2. **The OAuth refresh token rotates on every refresh.** Verified live: after forcing a refresh, the refresh-token value changed (SHA256 `cc0b3d0d52d5` → `bec5f1a84b22`), the access token changed, and `expiresAt` moved forward. The refresh token's own expiry (`refreshTokenExpiresAt`) stayed fixed (~3 weeks out), so it rotates in value but keeps a fixed absolute deadline. Consequence: a hard re-auth cliff roughly every 3 weeks that no automated refresh can paper over.
3. **The credential file** (`claudeAiOauth`) carries `accessToken`, `refreshToken`, `expiresAt`, `refreshTokenExpiresAt`, `scopes`, `subscriptionType`, `rateLimitTier`; siblings `mcpOAuth` and `organizationUuid` must be preserved by any writer.

Fact 2 is why an unconditional refresh from Watchtower is unsafe while a Claude Code session is live: rotating the shared refresh token out from under a running session invalidates the copy that session holds in memory, logging it out at its next refresh. This design avoids that entirely by never rotating under a live session.

## Goals

- The operator's first daily session window starts at a chosen time (default 07:00), fired automatically, even when the operator is away from the desk — provided the workstation is on and awake.
- Watchtower never starts a session window at an unchosen time.
- Watchtower never rotates the refresh token out from under a live Claude Code session.

## Non-goals

- No wake-from-sleep. If the machine is asleep or off at the anchor time, the anchor simply does not fire that day.
- No catch-up. If the anchor time is missed (machine asleep/off, Watchtower not running), there is no later make-up fire.
- No tray UI in v1 (config-file only).
- No change to the widget-snapshot push, the server, or the Android app.

## Design

### Part 1 — Remove the reactive refresh ("Option C")

In `QuotaService.Poll`:

- Drop **both** `RefreshViaCli` calls (the proactive-expiry path and the post-401 retry path).
- Watchtower continues the read-only usage `GET` every poll. When the token is expired, or the GET returns 401/403, Watchtower spends nothing and **retains the last-known usage** on the widget rather than blanking it. Usage does not change while the operator is idle, so the last-known number stays accurate, and the reset countdown continues to tick locally (`ScheduleCountdown` is already decoupled from the poll).
- **Retire `QuotaBackoff`** (`QuotaBackoff.cs` + `QuotaBackoffTests`). Its sole purpose was suppressing quota-consuming spawns while logged out; with no spawns in the poll path, it has no remaining job.

After this change, the daily anchor (Part 2) is the **only** code in Watchtower that ever spawns `claude -p .`.

### Part 2 — The daily anchor

**Trigger.** A new `_anchorTimer` in `AppHost` at 60-second resolution. Each tick evaluates a pure scheduling gate, then, only when the gate passes, offloads the decision to a background thread.

Pure gate (`DailyAnchorSchedule.ShouldEvaluate(now, anchorTime, grace, handledDate)`):

1. If the anchor is disabled → do not evaluate.
2. If `handledDate == today` (already handled today) → do not evaluate.
3. If local `now` is not within `[anchorTime, anchorTime + grace)` → do not evaluate.
4. Otherwise → evaluate now.

`grace` is a hard-coded 3-minute constant. The narrow window is what delivers "awake-only, no catch-up" with no extra logic: if the machine is asleep across the anchor minute and resumes later, no tick ever lands inside the window, so nothing fires; the next eligible tick is tomorrow.

**Decision (at fire time, in `QuotaService.TryRunDailyAnchor()` → `Fired | SkippedWindowOpen | Failed`).** Do one read-only usage `GET` with the current token:

- If it reports an **open session window** → return `SkippedWindowOpen`. A window is already anchored; firing would be redundant and would rotate the token under the live session that opened it.
- If it reports **no open window**, or returns **401/expired** → run the isolated headless turn and return `Fired`. (An expired token at the anchor time means no recent activity, hence no open window, so firing is correct; the turn both anchors the window and refreshes the token/display for the day.)
- On a transient failure (network, unresolved `claude`) → return `Failed`.

Doing the GET at fire time (rather than reading the last poll result) makes the anchor independent of whether the quota display is enabled, and gives an at-the-moment answer.

**"Open session window" inference.** Open iff the session window's reset time is in the future AND its utilization is above zero: `Session.ResetsAt > now && Session.Percentage > 0`. Confirmed live 2026-07-22: the usage endpoint populates `five_hour.resets_at` with a future timestamp and reports `utilization`; the `is_active` flag on the `limits` array marks the currently-binding limit (weekly, in the observed sample), NOT whether a session window is open, so it is deliberately not used. Requiring nonzero utilization biases an otherwise-unconfirmable state toward firing (the anchor's purpose) rather than wrongly skipping and missing the anchor.

**The spawn (`RunHeadlessAnchorTurn`, renamed from `RefreshViaCli`).** Reuse the exact isolated invocation validated live:

- Working directory = a temp dir (not Watchtower's cwd).
- `--setting-sources project` (exclude the user settings layer so the switchboard away-mode Stop hook / MCP server do not drive the throwaway turn into an `ask_human` phone ping).
- `--no-session-persistence`.
- `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` removed from the child environment.
- 30-second timeout; both output streams drained concurrently; killed on overrun.

**"Handled today" state.** In-memory only (`DateOnly? _anchorHandledDate` in `AppHost`), set on any non-transient outcome (`Fired` or `SkippedWindowOpen`). No persistence is required:

- After the 3-minute window passes, no tick can re-enter it until tomorrow, so a restart later in the day cannot double-fire.
- In the rare case Watchtower restarts *inside* the window, the re-decision's GET now sees the window the first fire just opened and returns `SkippedWindowOpen`. It self-corrects.

A `Failed` outcome does not mark the day handled, allowing a retry on the next in-window tick.

**Concurrency.** The tick offloads to `Task.Run` (like `PollQuota`), guarded by an `_anchorRunning` flag to prevent overlap; the blocking HTTP + up-to-30s spawn must not run on the UI thread.

### Part 3 — Configuration (`AppConfig`)

Two new fields:

- `DailyAnchorEnabled` (bool), default **true**.
- `DailyAnchorTime` (string `"HH:mm"`), default `"07:00"`. Parsed to a time-of-day; a malformed value falls back to `07:00` WITHOUT setting the degraded flag (as-built correction to the draft: `LoadDegraded` gates `SaveTo`, so coupling a bad time string to it would silently block all config saves).

The 3-minute grace is a constant, not a config field. No tray UI in v1; the operator sets these in `config.json`.

## Testing

- **Pure scheduling gate** (`DailyAnchorSchedule.ShouldEvaluate`), xUnit in `Switchboard.Watchtower.Core.Tests`, mirroring the existing `ScanGate` / `QuotaBackoff` pure-logic tests:
  - before the window → no
  - first tick inside the window → yes
  - inside the window but already handled today → no
  - asleep-across-window then resume after the window → no
  - next day (handledDate is yesterday) → yes
- **Config parse** for `DailyAnchorTime`: `"07:00"` parses; a malformed value falls back to `07:00` and leaves `LoadDegraded` false (a bad time string must not block config saves).
- **I/O path validated live, not mocked**, consistent with the repo (the usage GET and the spawn are not unit-tested today; `QuotaParser` and the pure gates are). Live gate before the work is called done:
  - Set the anchor ~2 minutes out; confirm it fires exactly once and starts a window.
  - Confirm a second in-window tick returns `SkippedWindowOpen` (no double fire).
  - Confirm skip-if-open by scheduling the anchor while a window is already open.
- Watchtower-only; the Python test suite is untouched.

## Risks and edge cases

- **Late-night window straddling the anchor.** If the operator worked until, say, 2am, that window may still be open at the anchor minute; the anchor then skips (a window is open). This is correct behavior and inherent to how windows roll; no special handling.
- **3-week refresh-token cliff.** When the refresh token itself expires, no automated turn can refresh it; the operator must re-auth. Watchtower simply shows last-known usage until a real session re-authenticates. Not introduced by this design; noted so it is not mistaken for a regression.
- **`claude` not resolvable.** The spawn no-ops and logs an error (existing behavior); the outcome is `Failed`, so the anchor retries on the next in-window tick and gives up for the day when the window passes.

## Decisions (settled during brainstorming)

1. Machine state: anchor fires only if the workstation is on and awake at the anchor time; no wake-from-sleep, no catch-up.
2. Already-active-at-anchor-time: skip (a window is already open), detected via the usage GET.
3. Scheduling mechanism: dedicated in-app Watchtower timer (not the quota poll tick, not an external Task Scheduler entry).
4. `DailyAnchorEnabled` default: true.
5. UI: config-file only for v1 (no tray toggle).
6. `QuotaBackoff`: retired.

## As-built notes (2026-07-22)

- **Spawn reports success/failure.** `RunHeadlessAnchorTurn` returns `bool` (true only when the process launches, exits within the 30s timeout, and returns exit code 0); `TryRunDailyAnchor` maps a false result to `AnchorOutcome.Failed`, which leaves the day un-handled so the anchor retries on the next in-window tick. This realizes the Risks section's `Failed`-on-spawn-failure retry, which the first implementation draft missed (it always returned `Fired`). False negatives self-correct: a retry does a fresh usage GET and skips if the earlier turn actually opened a window.
- **Open-window predicate tightened** to also require `Session.Percentage > 0` (see Part 2), after the live `/usage` sample showed `resets_at` is populated with a future value while `is_active` tracks the binding limit rather than window-open state.
- **Live validation:** the skip-if-open path was validated end to end on 2026-07-22 (the anchor evaluated at the configured minute, did a fire-time usage GET, and logged `SkippedWindowOpen` while a window was open, spending no turn; timer/gate/decision wiring and taskbar re-embed confirmed). The true `Fired` path (no window open) was deferred to a natural idle morning, since an open window cannot be cleared on demand while working.
