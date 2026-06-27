# Switchboard Operator and Watchtower Integration - Design

**Date:** 2026-06-15. **Status:** design approved 2026-06-15; ready for implementation plan. **Authors:** John + Claude (brainstorm). **Relation:** new surface. The dashboard and server changes land in this repo (Switchboard); the widget (now **Switchboard Watchtower**) has been moved into this same monorepo at the `watchtower/` subdirectory. Reads the live RTDB written by the Switchboard server. The verified path/shape contract is in the Appendix; do NOT build from `docs/switchboard-design-spec-comprehensive.md`, which has drifted from the live implementation (see Appendix note).

**Names (settled with John 2026-06-15):** the dashboard cockpit is **Switchboard Operator** ("Operator"); the existing taskbar widget has been renamed to **Switchboard Watchtower** and moved into this monorepo ("Watchtower"; namespaces `Switchboard.Watchtower` / `Switchboard.Watchtower.Core`, config root `%APPDATA%\Switchboard\Watchtower`, living at `watchtower/` in this monorepo). "Switchboard" remains both the MCP's own name and the family/suite name. Below, "the dashboard" means Operator and "the widget" means Watchtower. Watchtower launches Operator by opening its URL, optionally with a `#conv=<conversationId>` fragment that Operator consumes on load to open straight to that conversation.

## Goal

Two coordinated surfaces over the same Switchboard state:

1. An always-on, glanceable readout in the existing Watchtower client (a few numbers plus a launch button), so John can see "is an agent blocked on me / am I in away mode" without opening anything.
2. A launch-on-demand web "cockpit": a full read-and-write control surface for active conversations, members, agent activity, pending questions, and the spawn / resume / combine / force-end / away controls the phone already has, on the big screen.

The widget is the entry point; the dashboard is the depth.

## Decisions (settled with John, 2026-06-15)

1. **Integrate, do not subsume.** The widget stays an always-on Windows taskbar utility with its zero-dependency Core untouched. It gains a Switchboard stats line and a launcher in the UI layer only. The dashboard is a new, separate web app that the widget launches.
2. **Dashboard stack: a static web app served by the Python server.** The server already runs a Starlette/uvicorn app (`server/main.py`); serving the bundle is a `StaticFiles` mount, no new web framework.
3. **Front-end: Preact + htm, vendored as ESM, zero build.** No node toolchain enters the Python repo. A ~4 KB runtime vendored as a single file gives components and keyed reconciliation, so live RTDB pushes do not steal focus from the answer box or jump the transcript scroll.
4. **Data path is hybrid.** The dashboard talks to Firebase RTDB directly (realtime listener reads + the phone's command-tree writes), reusing the existing Google Sign-In identity. The widget never touches Firebase: it reads a tiny `GET /stats` JSON from the server. The dashboard reads the existing `GET /healthz` for the health panel, since listener/loop health and lifetime counters are in-memory only, not in RTDB.
5. **Interactivity: full cockpit.** The dashboard issues every write the phone does: answer a pending question, toggle away mode, spawn fresh, resume dormant, combine, force-end, hide/unhide.
6. **Layout: "mission control" (3-pane).** Left Conversations list, center conversation detail (member roster + agent status + lazy transcript + inline answer), right Commands rail. The left and right rails each collapse independently to a glanceable 44 px icon rail (conversation dots keep color + pending badges; commands become icons); the center pane reflows to fill.
7. **v1 scope: the live cockpit plus lightweight health from `/healthz`.** The heavier JSONL-derived analytics (average resolution time, answered-by-source, throughput, timeouts, rate-limit hits) and their required server-side aggregation endpoint are a deferred fast-follow, not v1.
8. **Prerequisite (plan task #1): capture, commit, and tighten the RTDB security rules.** They are currently managed in the Firebase Console out-of-band and are not in the repo. This is a blocking prerequisite for a direct-RTDB read-and-write client and needs John's Console access.

## Architecture

### Components

- **Dashboard web app** (`dashboard/` in this repo): static HTML/CSS/JS + a vendored Preact+htm runtime + the Firebase Web SDK (ESM). A Firebase RTDB client: realtime listeners in, command-tree writes out. Polls the server's `/healthz` for the health panel.
- **Server changes** (`server/`, this repo): (a) a `StaticFiles` mount that serves `dashboard/`; (b) a new `GET /stats` JSON endpoint for the widget; (c) committed + tightened RTDB rules (`database.rules.json` + `firebase.json`).
- **Widget integration** (Switchboard Watchtower at `watchtower/`, UI layer only): a `SwitchboardStatsReader` that polls `GET /stats`, a compact stats line + "Open dashboard" button in the DetailPanel, a tray-menu launcher, and an optional pending badge. Core library is not touched.

### Data flow

- **Live state in:** dashboard subscribes to RTDB via the Firebase Web SDK; updates arrive as push snapshots. Source paths: `conversations/*`, `global_settings/*`, `admin_notifications/*`.
- **Commands out:** dashboard writes the same RTDB command trees the phone writes; the server's existing dispatch loops consume them. No new server write code.
- **Server-sourced reads:** dashboard polls `GET /healthz` (listener/loop health, `total_answered`); widget polls `GET /stats` (4-5 numbers). Both are localhost, unauthenticated, like `/healthz` today.
- **Hosting:** the server serves the dashboard bundle. Launching the dashboard is opening its localhost URL.

## Design

### 1. RTDB security rules (prerequisite, plan task #1)

The live rules are not in the repo (no `database.rules.json`, no `firebase.json` rules). The Android app reads the whole `conversations` tree as a signed-in Google user, which is evidence the live rules at least permit `auth != null` reads, but this must be confirmed, not assumed. John's manual Console access is a precondition that must happen before any engineer starts the rest of the plan. Steps:

1. John opens the Firebase Console for project `jja-switchboard` and captures the current Realtime Database rules.
2. Commit them into the repo as `database.rules.json` + a `firebase.json` so the security model stops being invisible.
3. Tighten read and write to John's identity rather than any signed-in Google account, e.g. a top-level guard `".read"` / `".write"` keyed on `auth.token.email === 'you@example.com'` (or `auth.uid`). This matters more because the dashboard also writes command trees.
4. Add the dashboard's serving origin (`localhost`) to Console -> Authentication -> Authorized domains, or `signInWithPopup` is rejected.

**Done means:** (a) `database.rules.json` + `firebase.json` committed and syntactically valid; (b) a rules-simulator (or emulator) run confirms John's identity can read and write the dashboard paths and a foreign identity is denied; (c) Authorized domains updated. This task gates the dashboard's read/write feasibility and is done first.

### 2. Server changes

**Serve the bundle.** Add a `StaticFiles` mount (proposed route `/dashboard`) beside the existing route registrations in `server/main.py`. The bundle is plain static files; no build step runs at deploy.

**`GET /stats`.** A new localhost, unauthenticated route (same trust model as `/healthz` and `/away-mode`) returning a small JSON object computed from the in-memory `Registry`:

```json
{
	"active_conversations": 3,
	"pending_count": 2,
	"oldest_pending_age_seconds": 247,
	"away_mode": true,
	"healthy": true
}
```

Registry members available as-is (verified in `server/registry.py`): `pending_count` (property), `oldest_pending_age_seconds` (property/method), `total_answered` (attribute), `global_away_mode` (property). NEW work required: an active-conversation count, e.g. an `active_conversations_count` helper that returns `sum(1 for c in conversations.values() if c.state == 'active')`. `healthy` is a roll-up of the existing listener/loop supervisor health (true when no listener or dispatch loop is in a failed state). This endpoint exists solely so the widget never needs Firebase.

**Firebase web config.** The dashboard ships a `dashboard/dashboard-config.js` holding the non-secret Firebase web config (apiKey, authDomain, databaseURL, projectId, storageBucket), the same public values present in `android/app/google-services.json`. Recommendation: these are public web config (they ship to every browser), so commit them; the file is NOT gitignored. (The repo gitignores `google-services.json` because it is the canonical Google-issued artifact, not because these specific values are secret.) If John prefers them out of git, the fallback is to have the server inject them at the `StaticFiles` mount from its existing `FIREBASE_*` config.

### 3. Dashboard web app

**Auth.** `initializeApp(webConfig)` then `signInWithPopup(new GoogleAuthProvider())`, reusing the project's existing Web OAuth client. Listeners attach only once authenticated and re-attach on token refresh, mirroring the Android `IdTokenListener` pattern (`android/.../MainViewModel.kt`). A signed-out or denied state renders an explicit sign-in gate, never a blank screen.

**Single schema module.** All RTDB path builders and record field names live in one module (`dashboard/schema.js`). This confines the schema coupling that direct-RTDB reads imply to one file, and a unit test pins the paths so drift is caught. The Appendix is the authoritative contract this module encodes. Rationale: the comprehensive spec already drifted from the live paths, so the live paths are treated as the source of truth and centralized.

**Reads (realtime listeners), shallow-first.** The dashboard does NOT replicate the phone's whole-tree listener (it pulls every message and the append-only, never-truncated `members_history`). Instead:

- Subscribe to `conversations` at the child level for `meta` only, to drive the left list (title, state, preview, `last_activity_at` for sort, `hidden` to filter).
- Subscribe to `conversations/<id>/pending_questions` across active conversations. The per-conversation pending badge and the global pending count are derived by enumerating these children; the dashboard does NOT use the `pending_responses` mirror counter for this. `unread_count` MAY drive an optional unread dot but is not required for v1.
- Subscribe to `global_settings/{away_mode, open_conversation_id, wsl_available}` and `admin_notifications`.
- Lazily subscribe to the selected conversation's `messages`, `members_active`, and `agent_status` only while it is open in the center pane; unsubscribe on switch.

**Derivation (pure functions, ported from Android `ConversationPolicy`).** Member state: `alive == true` -> alive; `alive == false && session_lost_permanently == false` -> dormant; `session_lost_permanently == true` -> lost. Conversation active vs ended from `meta.state`. Pending aggregation = sum of `pending_questions` child counts across active conversations. Oldest-pending age: `pending_questions` records carry no timestamp, only a `msgId`. The dashboard resolves each `msgId` to the referenced message's `timestamp` (available once that conversation's messages are subscribed) and computes age from there; a pending item whose `msgId` does not resolve falls back to the time it was first observed in the listener snapshot. These functions are unit-tested in isolation.

**Writes (full cockpit), using the verified Appendix paths:**

- Answer: write `conversations/<id>/answers/<request_id>` = `{text, sender, request_id, written_at}` (the server's answers listener resolves the pending future and deletes the node).
- Away ON: push `away_mode_commands/<id>` = `{type:'enter_global', issued_at}`.
- Away OFF: push `away_mode_commands/<id>` = `{type:'exit_global', issued_at, decision?, default_text?}`. Turning away off is not a plain boolean: if pending questions exist, the UI prompts for the bulk-respond decision (`send_default` with `default_text`, `skip`, or `cancel`), exactly as the phone does; the server applies the decision and only then flips the flag false.
- Spawn fresh: push `spawn_commands/<id>` = `{type:'fresh', surface, project, issued_at, prompt?, target_conversation_id?}`.
- Resume: push `spawn_commands/<id>` = `{type:'resume', source_conversation_id, issued_at, prompt?}` (no surface/project/target; the dormant conversation supplies them).
- Combine: push `combine_commands/<id>` = `{source_conversation_id, target_conversation_id, issued_at}`.
- Force-end: push `force_end_commands/<id>` = `{conversation_id, issued_at}`.
- Hide / unhide: set `conversations/<id>/meta/hidden` = boolean.

The server validates and dispatches these exactly as it does the phone's.

**Command UI scoping.** Commands act on the selected conversation except fresh spawn:

- Spawn (fresh): a dialog with Surface (windows/wsl), Project (path), optional Prompt, and optional Target conversation (default: none, i.e. a brand-new conversation).
- Resume: a picker of dormant conversations (all members dormant, resumable); selecting one with an optional new prompt issues a resume command keyed on its `source_conversation_id`.
- Combine: from the selected conversation, pick a target conversation; issues combine with `source` = selected, `target` = chosen.
- Force-end: acts on the selected conversation; requires an in-UI confirmation before the command is pushed.
- Away toggle: ON issues `enter_global`; OFF issues `exit_global` with the bulk-respond decision above.

**Health/metrics panel.** Poll `GET /healthz` on an interval (proposed 5 s) for listener/loop state and `total_answered`. Render a compact health dot in the top bar plus a small metrics line. (JSONL analytics deferred per Decision 7.)

**UI (layout C).** Top status bar: away pill (clickable toggle), global counts (active / pending / oldest), health dot, WSL indicator. Left Conversations rail (collapsible). Center detail pane: member roster with per-member state dot + surface + agent-status, lazy transcript (markdown render, honoring `cancelled`/`rejected` flags), inline answer box scoped to the selected pending question. Right Commands rail (collapsible): spawn / resume / combine / force-end / away toggle. Each rail collapses independently to a 44 px icon rail; the collapsed state persists in `localStorage`.

**State and reactivity.** A small Preact store holds the projected view-model; RTDB listener callbacks update the store; Preact's keyed reconciliation patches the DOM. Components are keyed by identity (message list by `msg_id`, the answer box by `request_id`, the member roster by `sender`), which is what preserves input focus and scroll position when a snapshot arrives, and is the reason for Decision 3.

### 4. Widget integration (Switchboard Watchtower, UI layer only)

- **`SwitchboardStatsReader`** (testable, UI-layer): an HTTP GET to `/stats` on a timer (reuse the existing scan cadence or a dedicated interval), parsing the 4-5 numbers. No Firebase SDK, no auth, no credential store, no Core change.
- **DetailPanel:** a compact line, e.g. `Switchboard: 3 active - 2 pending - away ON`, plus an "Open dashboard" button. Degrades to `Switchboard: unavailable` when `/stats` fails.
- **TrayIcon menu:** "Open Switchboard dashboard" launches the dashboard URL in the default browser. Optional config-gated tray badge when `pending_count > 0`.
- **Config:** extend the (post-rename) `%APPDATA%\Switchboard\Watchtower\config.json` with a `switchboard { enabled, statsUrl, dashboardUrl, showBadge }` block. This integration is authored against the `Switchboard.Watchtower` namespace and lands on top of the Watchtower rename (a separate effort owned by the Watchtower agent); the launcher opens `dashboardUrl` and may append `#conv=<conversationId>` to deep-link Operator. `enabled` gates the entire Switchboard UI (stats line, launcher, badge); when false the block is hidden. `statsUrl` / `dashboardUrl` default to `http://localhost:9876/stats` and `http://localhost:9876/dashboard` but are configurable; a WSL-hosted server requires pointing them at the Windows host IP, consistent with the existing `SWITCHBOARD_BASE_URL` pattern. Changes apply on widget restart.

## Error handling and edge cases (fail loudly)

- **Auth failure / token expiry:** explicit sign-in gate; re-attach listeners on refresh. No silent blank state.
- **Rules deny a read/write, or server unreachable:** the affected pane renders a persistent inline banner (e.g. "Cannot read conversation details: check RTDB rules and sign-in") with a Refresh/retry action, not a blank pane. For server-unreachable specifically, the live RTDB view keeps rendering (it is decoupled), but the `/healthz` panel shows "server unreachable" and the UI warns that issued commands queue until the dispatch loops return (an honest signal, since a stale view that looks live would mislead). The widget `/stats` line shows "unavailable".
- **Ephemeral pendings:** `child_added` / `child_removed` drive the queue; a `request_id` vanishing on supersede or answer is expected and handled, not an error.
- **`members_active` keyed by sender (display name):** a rename shifts the key; render off whatever keys exist, do not cache by stale name.
- **Destructive writes:** force-end and combine require an in-UI confirmation before the command is pushed.

## Testing

- **Dashboard derivation:** unit-test the pure functions (member-state classification, pending aggregation, oldest-age) against mock RTDB snapshots; port the Android `ConversationPolicy` test cases. Unit-test `schema.js` path builders (pins the contract). Tests run with Node's built-in runner (`node --test`) to honor the zero-build constraint; no Jest/Vitest/Mocha and no `node_modules`. Browser/listener behavior is checked manually against the Firebase emulator.
- **Server `/stats`:** unit-test against a fake Registry covering active/ended counts, pending, away, and the `healthy` roll-up; assert consistency with `/healthz` semantics. Live check: `curl http://127.0.0.1:9876/stats`.
- **Widget reader:** unit-test the `/stats` parser (in the style of the existing `UsageReader` tests); manual run for the panel line, the launcher, and the unavailable fallback.
- **Rules:** validate with the Firebase rules simulator or emulator per Section 1's acceptance criteria (John's identity can read/write the dashboard paths; another identity is denied).
- **Manual integration:** run the dashboard against the live RTDB read-only first, then exercise one write of each command type end-to-end (answer, away on/off, spawn, resume, combine, force-end, hide) and confirm the server dispatches it.

## Phasing

- **v1:** rules (task #1) -> `/stats` + bundle serving -> dashboard read cockpit (list, detail, lazy transcript, roster, agent status, pending queue, away/open/wsl, admin feed, `/healthz` health) -> command writes (answer, away, spawn, resume, combine, force-end, hide) -> widget stats line + launcher.
- **Parallelism:** rules (task #1) must complete first. After that, `/stats`, the dashboard app, and the widget integration can proceed in parallel; suggested merge order is `/stats` (smallest, no deps) -> dashboard (needs `/stats` + rules) -> widget (needs both server endpoints).
- **Deferred fast-follow:** JSONL analytics panel + a server-side aggregation endpoint (average resolution time, answered-by-source, throughput, timeouts, rate-limit hits); tray-badge polish; any multi-user/identity work beyond John.

## Security notes

- The Firebase web config is public (it ships to the browser); the actual access control is the RTDB rules, which is why task #1 tightens them to John's identity. Do not rely on the config being secret.
- The dashboard is served and reached over localhost; `/stats` and `/healthz` stay unauthenticated on the localhost trust boundary, consistent with the current server.
- The dashboard's command writes are as powerful as the phone's; the rules' write side must be scoped to John's identity as part of task #1.

## Out of scope / follow-ups

- JSONL analytics and its aggregation endpoint (deferred fast-follow, Decision 7).
- Correcting `docs/switchboard-design-spec-comprehensive.md` answer/pending/away path drift is a small side cleanup, tracked separately; this design simply does not depend on that doc.
- Off-host access (dashboard or widget running on a different machine than the server) is not designed for here beyond the widget's configurable URL; both assume same-host localhost in practice.
- Multi-user / shared dashboards are explicitly out (single-user, John's identity).

## Files touched (anticipated)

| File / area | Repo | Change |
|-------------|------|--------|
| `database.rules.json`, `firebase.json` | Switchboard | New: captured + tightened RTDB rules (task #1) |
| `server/main.py` | Switchboard | Two logical changes: register a `StaticFiles` mount for `dashboard/`; add the `GET /stats` route |
| `server/registry.py` | Switchboard | New `active_conversations_count` helper; `/stats` otherwise reads existing properties |
| `dashboard/` (index.html, app.js, schema.js, store, components, styles, vendored htm-preact.js, dashboard-config.js) | Switchboard | New: the cockpit web app (dashboard-config.js committed, not gitignored) |
| `dashboard/*.test.js` (run via `node --test`) | Switchboard | New: derivation + schema-path unit tests |
| `tests/test_stats_endpoint.py` | Switchboard | New: `/stats` unit tests |
| `AppHost.cs` / `TrayIcon.cs` / `DetailPanel.cs` (UI layer) | watchtower/ | Stats line, "Open dashboard" launcher, optional badge |
| `SwitchboardStatsReader.cs` (+ test) | watchtower/ | New: `/stats` poller + parser |
| `config.json` / `AppConfig` | watchtower/ | New `switchboard { enabled, statsUrl, dashboardUrl, showBadge }` block |

## Appendix: verified RTDB schema (the contract the dashboard codes against)

Captured from `server/firebase.py` write paths, `server/gateway/dispatch.py` command consumers, and the Android read/write code (`MainViewModel.kt`); this is the live truth, not the comprehensive spec. The comprehensive spec documents `conversation_answers/<id>/<sender>/<push_id>`, an `away_mode_commands` `{type:'set'}` shape, and differing field names; the live code uses the paths and shapes below. Build `schema.js` and the rules from this table.

**Reads:**

| Path | Shape |
|------|-------|
| `conversations/<id>/meta` | `{title, state: 'active'\|'ended', continued_from, created_at: float, last_activity_at: float, ended_at, hidden: bool, preview}` |
| `conversations/<id>/members_active/<sender>` | `{cli_session_id, sender, cwd, surface: 'windows'\|'wsl', alive: bool, session_lost_permanently: bool, session_ended_at, session_end_reason, joined_at: float, last_seen_seq: int}` |
| `conversations/<id>/members_history/<sender>` | same as members_active plus `left_at` (append-only, unbounded; read lazily only if showing departed members) |
| `conversations/<id>/pending_questions/<request_id>` | `{sender, questionText, cancelled: bool, msgId, suggestions}` (no timestamp; resolve age via msgId -> message timestamp) |
| `conversations/<id>/agent_status/<sender>` | `{state, detail, updated_at}` (state e.g. `thinking`, `waiting`, `tool:<name>`; a `clear` write deletes the node, so absence == idle) |
| `conversations/<id>/messages/<msg_id>` | `{type, sender, text, format: 'plain'\|'markdown', timestamp: ISO-8601, cancelled, rejected, title?, request_id?, url?, filename?, suggestions?, attached_to_msg_id?}` (lazy, selected conv only) |
| `conversations/<id>/unread_count` | int (non-human messages only; optional unread dot) |
| `conversations/<id>/pending_responses` | int (server mirror counter; not used by the dashboard) |
| `global_settings/away_mode` | bool |
| `global_settings/open_conversation_id` | string conv_id or null |
| `global_settings/wsl_available` | bool |
| `admin_notifications/<push_key>` | `{sender: 'system', type: 'notify', text, format: 'markdown', timestamp: ISO-8601}` |

**Writes (command trees; the server's dispatch loops consume and delete them). Verified against `MainViewModel.kt` writers and `dispatch.py` consumers:**

| Path | Shape |
|------|-------|
| `conversations/<id>/answers/<request_id>` | `{text, sender, request_id, written_at}` |
| `conversations/<id>/meta/hidden` | bool |
| `away_mode_commands/<push_id>` (away ON) | `{type: 'enter_global', issued_at: ISO-8601}` |
| `away_mode_commands/<push_id>` (away OFF) | `{type: 'exit_global', issued_at: ISO-8601, decision?: 'send_default'\|'skip'\|'cancel', default_text?: string}` |
| `spawn_commands/<push_id>` (fresh) | `{type: 'fresh', surface: 'windows'\|'wsl', project, issued_at, prompt?, target_conversation_id?}` |
| `spawn_commands/<push_id>` (resume) | `{type: 'resume', source_conversation_id, issued_at, prompt?}` |
| `combine_commands/<push_id>` | `{source_conversation_id, target_conversation_id, issued_at}` |
| `force_end_commands/<push_id>` | `{conversation_id, issued_at}` |

**Not in RTDB (read from the server, not Firebase):** listener/dispatch-loop health and `total_answered` (in-memory, via `/healthz`); the widget's roll-up (via `/stats`); all JSONL-derived analytics (deferred).
