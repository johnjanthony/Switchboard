# Next session - Switchboard Operator

Branch: `session_id-as-key`. Last updated 2026-06-16.

## Where things stand

**Switchboard Operator (the web cockpit) and its Watchtower integration are committed** at `81b02c4` ("Switchboard Operator web cockpit + Watchtower /stats integration"), ledgered as **T-160**. Built from the approved spec + plan via subagent-driven development (one implementer + a spec review + a code-quality review per task; 23 tasks across Phases 1-4; the Phase 0 RTDB rules shipped earlier at `8f52423`).

Tests green at ship: dashboard `node --test dashboard/` 73 pass; Watchtower `dotnet test` Core 115 pass and `dotnet build` 0/0; server registry + `/stats` + main-route suites pass. The server-side HTTP layer (`/stats` 5-key roll-up, `/dashboard` serving the full module graph) is live-verified against the running server.

## The one thing left: manual e2e smoke test (browser + widget)

NOT yet done. It needs Google Sign-In and a running widget, so it is a human-in-the-loop pass. A self-contained smoke-test prompt was handed off for a fresh session; it embeds both checklists:
- Dashboard (browser at `http://localhost:9876/dashboard`): sign in, then list / detail / answer a pending / away-off-with-pendings / hide-unhide / spawn-resume-combine-force-end / `#conv=` deep-link / rail-collapse persistence.
- Watchtower: set `Switchboard.Enabled=true` (and `ShowBadge=true`) in `%APPDATA%\Switchboard\Watchtower\config.json`, launch the widget, verify the stats line / Open-dashboard button / tray launcher / pending badge / "unavailable" fallback.

Writes hit the live system (answering resolves a real `ask_human`; spawn launches a real agent; combine and force-end are destructive), so use throwaway conversations and confirm destructive steps with John first.

## Deviations from the as-drafted plan (deliberate, reviewed, shipped)

- **Phase 3 resolutions:** Resume acts on the selected conversation (resumable = all members dormant), no global picker; admin-notifications render as a minimal strip under the StatusBar; ConversationList marks the `openConversationId` row with an `open` accent.
- **TrayIcon.cs** omits the plan's `using System.Diagnostics;` (it would be unused; `Process.Start` lives in AppHost).
- **AppHost.cs** wires the launcher events as `() => OpenDashboard()` lambdas (the direct `+= OpenDashboard` fails CS0123: an optional param is incompatible with a parameterless `Action`).
- In-session follow-ups closed: `markdown.js` link-scheme allowlist (http/https/mailto only); removed a dead `import * as paths` from ConversationList.

## Pointers

- Spec (its Appendix is the authoritative RTDB contract): [`superpowers/specs/2026-06-15-switchboard-dashboard-design.md`](superpowers/specs/2026-06-15-switchboard-dashboard-design.md)
- Plan (gitignored scaffolding): [`superpowers/plans/2026-06-15-switchboard-dashboard.md`](superpowers/plans/2026-06-15-switchboard-dashboard.md)
- Execution handoff (history): [`2026-06-15-operator-execution-handoff.md`](2026-06-15-operator-execution-handoff.md)
- Project shape now documents `dashboard/` and `/stats`: [`../AGENTS.md`](../AGENTS.md)
- Journal entry + ledger row: 2026-06-16 / T-160.

## Earlier work on this branch (shipped, for context)

The prior resumption notes that lived in this file (the P0-P5 remediation pass, Stream A T-148..T-151, and the P4 Wear rebuild) all shipped earlier on this same branch; see ledger rows T-152..T-159 and the 2026-06-12..2026-06-15 journal entries. Still deferred / separate: P3 (T-141) and T-157.
