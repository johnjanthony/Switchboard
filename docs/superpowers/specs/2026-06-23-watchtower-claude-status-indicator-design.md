# Watchtower Claude service-status indicator - design

**Date:** 2026-06-23
**Status:** Implemented 2026-06-23 (T-179)
**Scope:** Watchtower (.NET) only. No server, hook, Python, or NSSM-service changes.

## Problem

When a Claude Code session starts throwing API errors (overloaded 529s, 500s, rate-limit blips), the first question is always "is it me or is it Anthropic?" Watchtower already lives on the taskbar as the at-a-glance health surface for Claude work (context rings, plan usage, Switchboard pending). It should also answer that question by surfacing Anthropic's published service status from the Claude status page, and let John watch an in-progress incident until it clears.

## Decisions taken during brainstorming

- **Manual-only trigger.** Claude Code exposes no hook that fires on an API error (its hooks are lifecycle events: UserPromptSubmit, PreToolUse, PostToolUse, Stop, SessionStart/End, etc.), so there is no reliable automatic "an error happened" signal. John initiates the status check manually. No transcript-scanning, no automatic detection.
- **Loop lives in Watchtower (.NET), not the server.** A manual feature whose loop runs while the widget is open (which is effectively always) does not justify a Python server change, a new endpoint, or a service restart. The logic sits alongside the existing `QuotaService` / `SwitchboardStatsReader` fetch-and-display pattern.
- **Widget-strip dot appears only when actively watching, and is sticky until acknowledged.** A persistent green dot on the tiny, already-busy taskbar strip is noise. The dot appears when a watch begins and stays visible (showing the latest status) until John clears it, so an incident that resolves while he is away leaves a green "this happened, now clear" marker rather than silently vanishing.

## Source of truth

`GET https://status.claude.com/api/v2/summary.json` - a single request that carries both the overall rollup (`status.indicator`, `status.description`) and the unresolved-incident names (`incidents[]`) for a useful tooltip. Chosen over the tinier `status.json` (rollup only) precisely for the incident names.

The `indicator` field has four documented values, mapped to a `ClaudeStatusLevel` enum:

| `indicator` | `ClaudeStatusLevel` | Meaning |
|-------------|---------------------|---------|
| `none`      | `Operational`       | All Systems Operational |
| `minor`     | `Minor`             | Degraded performance |
| `major`     | `Major`             | Partial outage |
| `critical`  | `Critical`          | Major outage |
| (anything else / fetch failure) | `Unknown` | Unreachable or unparseable |

## Architecture and data flow

The feature reuses the established three-layer Watchtower pattern (a Core model + parser, a UI-project HTTP reader, an `AppHost` timer that pushes to the display surfaces):

```
ClaudeStatusReader.FetchAsync  ──►  ClaudeStatus.Parse  ──►  ClaudeStatus (Core record)
        ▲                                                          │
   _claudeStatusTimer (AppHost, normally stopped)                  ▼
   + CheckClaudeStatusNow() / Acknowledge()            _widget / _panel / _tray
        │
   ClaudeStatusWatch (Core, pure state machine)  ◄──── drives the above
```

### Components

1. **`ClaudeStatus` (Core record).** Immutable parse result. Fields: `Level` (the enum above), `Description` (e.g. "All Systems Operational"), `IncidentNames` (names of incidents whose `status` is not `resolved`/`postmortem`), `FetchedAt`. `Parse(string json)` returns `ClaudeStatus?` - null on malformed JSON or a missing/ill-typed `status.indicator`, mirroring `SwitchboardStats.Parse`. A null parse is surfaced as `Level == Unknown` by the reader layer.

2. **`ClaudeStatusReader` (UI project).** Thin glue over `ClaudeStatus.Parse`, mirroring `SwitchboardStatsReader`: `FetchAsync(CancellationToken)` GETs `summary.json` on a shared `HttpClient` (10s timeout), returns the parsed `ClaudeStatus`, or a `ClaudeStatus` with `Level == Unknown` when the server is unreachable / returns non-success / the body does not parse.

3. **`ClaudeStatusWatch` (Core, pure state machine).** Holds the watch state and the latest `ClaudeStatus`, and exposes pure transitions so the branching is unit-testable rather than buried in `AppHost` timer glue. It does not do I/O. Inputs are events (`RequestCheck`, `ApplyFetch(ClaudeStatus)`, `Acknowledge`, `Tick(now)`); outputs are decisions the host acts on (start/stop polling) plus the derived display state (dot visibility, dot color, button label).

4. **`AppHost` wiring.** A normally-stopped `System.Windows.Forms.Timer _claudeStatusTimer` (interval = `WatchIntervalSeconds`), a `_claudeStatusScanning` re-entrancy guard, a `CheckClaudeStatusNow()` and an `AcknowledgeClaudeStatus()` entry point, and a fire-and-forget `PollClaudeStatus()` that marshals the result back to the UI thread and feeds it into `ClaudeStatusWatch` - structurally identical to `PollQuota()` / `PollSwitchboard()`.

## Watch-loop state machine

| State | Polling | Widget dot | Entered from |
|-------|---------|------------|--------------|
| **Idle** | stopped | hidden | initial; or `Acknowledge` from any state |
| **Watching** | every `WatchIntervalSeconds` | visible, severity color (latest non-operational level) | Idle, when a manual check returns minor/major/critical |
| **ResolvedUnacked** | stopped | visible, green | Watching, when a poll returns Operational |
| **CappedUnacked** | stopped | visible, last-known severity color | Watching, when the `MaxWatchMinutes` safety cap is reached while still degraded |

Transitions:

- **Manual check from Idle** (`CheckClaudeStatusNow`): one immediate fetch. Operational result → stay Idle (no dot, no loop); the popup section still updates to show "All Systems Operational, checked just now." Degraded/outage result → Watching (start the timer, show the dot). A fetch failure (`Unknown`) on this initial one-shot stays Idle and shows the unreachable state in the popup only; it does not start a loop.
- **While Watching**, each tick re-fetches. A confirmed Operational result → ResolvedUnacked (stop the timer, dot turns green). A fetch failure (`Unknown`) is treated as "still unknown" and the loop continues - only a confirmed Operational stops it. Reaching `MaxWatchMinutes` of continuous watching → CappedUnacked (stop the timer; the dot persists at last-known severity).
- **Acknowledge / Stop** (the contextual popup button and tray item): from Watching it is an explicit dismiss; from ResolvedUnacked / CappedUnacked it clears the marker. In every case → Idle (timer stopped, dot hidden).

The dot persists (showing the latest known status) from the moment a watch begins until John acknowledges it; polling is the separate concern that stops on Operational, the safety cap, or a manual stop.

## UI surfaces

- **Hover popup (`DetailPanel`) - the rich surface.** A new "Claude status" group panel (group 4, same rounded-surface group-box treatment as the quota / sessions / Switchboard groups). Contents: a severity-colored status dot, the description text, active incident name(s) when present, a "checked Nm ago" relative timestamp (reusing `RelativeTime`), and a contextual button that mirrors the existing "Open dashboard" button styling. The button reads **Check now** in Idle, **Stop watching** in Watching, **Clear** in ResolvedUnacked / CappedUnacked. The group is always shown once the feature has any data to report (like the quota group); before the first check it can show a neutral "not checked" line with the Check-now button.
- **Tray menu.** A context-menu item mirroring the popup button's contextual label: "Check Claude status" (Idle) / "Stop watching Claude status" (Watching) / "Clear Claude status" (resolved/capped), placed near the existing "Refresh now" / "Open Switchboard dashboard" items.
- **Widget strip (taskbar).** A small severity-colored dot, drawn only when the watch state is not Idle (Watching / ResolvedUnacked / CappedUnacked). Color: severity gradient while degraded, green when resolved. It is display-only (no hit-testing), consistent with the existing pending badge; acknowledgement happens through the popup button or the tray item. Placement coexists with the amber Switchboard pending badge (distinct corner / offset so the two never overlap).

Color mapping for the dot and popup uses the widget's existing rings palette intent: green (operational), yellow (minor), orange/red (major), red (critical), grey (unknown/unreachable).

## Config

A small `ClaudeStatusConfig` on `AppConfig` (mirroring `SwitchboardConfig`), all with sensible defaults so an absent config block needs no migration:

- `SummaryUrl` - default `https://status.claude.com/api/v2/summary.json`.
- `WatchIntervalSeconds` - default `60` (polite for a Statuspage poll; floored at a small minimum).
- `MaxWatchMinutes` - default `180` (safety cap so a forgotten loop cannot poll indefinitely).

No enable-gate: the feature is invisible when Idle (no dot, no traffic), so there is nothing to switch off.

## Error handling

- All fetch failures collapse to `Level == Unknown` (the reader never throws to the caller; it logs via the existing `LogError` sink and returns Unknown), matching the "keep calm, show last-known / unavailable" behavior of `PollQuota` and `PollSwitchboard`.
- An `Unknown` result never stops a running watch loop (only a confirmed Operational does), so a transient network blip during an outage does not prematurely declare "resolved."
- Logging and config-save use the existing `LogInfo` / `LogError` / `SafeSaveConfig` helpers; logging must never crash the widget (existing convention).

## Testing

- **Unit tests (xUnit, `Switchboard.Watchtower.Core.Tests`):**
  - `ClaudeStatus.Parse`: each `indicator` value → correct `Level`; incident extraction (only non-resolved incidents); malformed JSON → null; missing / ill-typed `status.indicator` → null; empty `incidents` array.
  - `ClaudeStatusWatch`: the full transition table above - Idle→Watching on degraded check; Idle stays Idle on operational check; Watching→ResolvedUnacked on operational poll; Watching stays Watching on Unknown poll; Watching→CappedUnacked at the cap; Acknowledge→Idle from each non-Idle state; dot visibility / color / button-label derivations for each state.
- **Manual verification:** `ClaudeStatusReader` (the Core test project cannot reference the UI assembly) and the `AppHost` timer wiring - same convention documented on `SwitchboardStatsReader`. Verify against the live `summary.json` at least once (read-only, idempotent, non-billing) so the parser is checked against the real response shape, not just a fixture.

## Out of scope (YAGNI)

- Automatic error detection (ruled out: no API-error hook).
- Surfacing Claude status on the phone or in Operator (this is a Watchtower-local, at-desk signal).
- Scheduled-maintenance / component-level breakdowns (the rollup `indicator` + unresolved incident names are enough for the "is it me or Anthropic?" question).
- Persisting watch state across a widget restart (a restart returns to Idle; acceptable for a manual, transient signal).
