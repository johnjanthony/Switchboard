# Switchboard — Agent Orientation

Switchboard is the mission-control hub for a multi-agent workstation: it tracks Claude Code sessions and conversations, routes questions and notifications to a phone, mirrors telemetry to ambient surfaces (Operator, Watchtower), and dispatches phone-issued commands (spawn, combine, away mode). Its founding feature - and still the core protocol - is away mode: agents pause mid-task and request human input, answered from the phone.

This file orients any agent (Claude Code, etc.) working **on Switchboard itself** or **consuming** its tools. Switchboard ships as a Claude Code plugin; see [Setup](#setup) below.

Design history lives under [`docs/`](docs/); the current end-to-end design is [`docs/switchboard-design-spec-comprehensive.md`](docs/switchboard-design-spec-comprehensive.md), and the dated specs under [`docs/superpowers/specs/`](docs/superpowers/specs/) record how each subsystem evolved. The newest dated spec wins where two overlap.

## Project shape

Single Python process, one asyncio event loop, MCP HTTP server on `localhost:9876`, with a Firebase backend (Android + Realtime Database). The server also serves **Switchboard Operator**, a launch-on-demand web cockpit (a zero-build Preact+htm app in `dashboard/`), at `/dashboard`, plus a small widget-facing `GET /stats` roll-up. The dashboard talks to Firebase RTDB directly and reads `/healthz` for its health panel.

Away mode is the founding feature, not the whole product: ask/notify blocking semantics are away-mode-scoped (at-desk interaction uses the terminal), while session tracking, telemetry fan-out, Operator, and Watchtower are always-on ambient surfaces.

The Registry is in-memory. The pending-request index is keyed by `(conversation_id, cli_session_id)` tuples where `conversation_id` is a `conv-<uuid>` string from `Registry.conversations` — not a filesystem path; answers resolve by `(conversation_id, request_id)`. A second index under the same key shape holds each session's one non-blocking `ask_human(background=true)`; the two never supersede each other, and every count/lifecycle accessor reads their union. Pending ask_human futures die on restart, but the questions survive: hydration rebuilds pending_questions records as parked (future-less) pendings, an arriving answer resolves them with a history write plus a session notice, and unanswered ones expire at the 72h retention horizon (chunk 7). Conversations (the persistence unit) survive restart via Firebase hydration — see `server/hydration.py`.

## Layout

```text
server/
  __init__.py          Package marker
  __main__.py          Enables `python -m server`
  main.py              Entry point — wires config, registry, backend, MCP, uvicorn
  http_auth.py         TokenAuthMiddleware - shared-secret Bearer gate (loopback peers and /healthz exempt; active when SWITCHBOARD_TOKEN is set)
  config.py            Env-based Config loader (dotenv fallback)
  registry.py          PendingRequest + Registry (in-memory); conversations dict with members/pendings keyed by cli_session_id; session_to_conversation_id routing map; per-session asyncio.Lock for race-free first-call conv creation; away-mode flag
  session_registry.py  SessionRecord + SessionRegistry (session roster; push-fed; sweeper rules: inbox-poll heartbeat for polling sessions, silence for the rest, LivenessGrace after a start or a sleep)
  telemetry.py         Field-by-field validation of the mod's telemetry reports (POST /sessions/{sid}/telemetry)
  quota_store.py       QuotaStore: the account's plan usage (5-hour and 7-day windows) as the mods last reported it; mirrored to RTDB quota, zeroed when a window's reset passes
  model_label.py       Session model chip text: friendly model names + observed-else-spawn-pick resolution, published on sessions/ records as model_label / model_source
  messenger.py         Backend lifecycle base + 3 trait ABCs (MessageWriter, ResponsePoller, AwayModeMirror) + ConversationStore protocol + IncomingResponse
  firebase.py          FirebaseBackend (implements every messenger surface); Firebase admin logic (FCM, Realtime DB)
  spawn.py             Agent session spawner (triggered from Android app)
  spawn_catalog.py     Claude Code model/effort catalog (curated list; published to spawn_options/, dispatch validation allowlist)
  conversation_ops.py  Conversation lifecycle helpers (create, add/migrate member, queue-for-intro, wake, combine, session-fallback); sender-collision auto-disambiguation ('Claude Win' -> 'Claude Win 2' etc.)
  cli_session_end.py   handle_session_end: marks a member dormant on session end; invoked by the POST /session_end route
  rate_limiter.py      Per-conversation token-bucket rate limiter consumed by ask_human, notify_human, send_document_human, and message_and_await_agent / post_agent_message (which degrade to FCM suppression instead of rejecting)
  canonicalization.py  Canonical-cwd normalization (display-only; cwd is a display tag)
  logging_jsonl.py     JSONL audit log
  inbound.py           Shared inbound human-to-agent delivery ladder (deliver_human_message for free-form phone messages, deliver_background_answer for background-ask answers); per member: resolve a live blocking ask -> wake a wait -> queue a session notice
  hydration.py         Rebuilds Registry state from Firebase on startup (conversations survive restart)
  rules_audit.py       Startup audit of the deployed RTDB rules (placeholder/test-mode detection; loud, non-fatal)
  firebase_supervisor.py  SupervisedListener + LoopSupervisor (Firebase-listener / dispatch-loop supervision for /healthz)
  session_fallback.py  Session-to-conversation fallback resolution (home-conversation rebind / unbind)
  command_freshness.py Staleness gate for queued Firebase command entries (COMMAND_TTL_SECONDS)
  claude_status.py     Claude service-status watch (poll loop + status parse published to widget/status)
  build_info.py        Running-service identity for /healthz (start time, server/ source fingerprint, git HEAD); the smoke harness keeps a pinned copy of the fingerprint; mod_fingerprint over plugin/hooks for the stale-mod alarm
  gateway/             Tool handlers + dispatch loops
    handlers.py          ask_human, notify_human, send_document_human, message_and_await_agent, post_agent_message, join_conversation, combine_conversations, lookup_conversation_ids, leave_conversation, set_away_mode tool closures; JSON status envelopes (_envelope/_terminal_envelope/_wrap_wait_result)
    dispatch.py          dispatch_responses, dispatch_combine_commands, dispatch_force_end_commands, dispatch_spawn_commands, dispatch_away_mode_commands, dispatch_message_commands, dispatch_status_request_commands, dispatch_session_sweep, dispatch_conversation_sweep, dispatch_presence, handle_force_end
    document.py          _validate_path + extension allowlist + secret-name denylist + sha256 helpers
    bulk_respond.py      _apply_bulk_respond_decision (used by exit_global to drain pending questions)
    parked.py            finish_parked_resolve - bookkeeping for resolving a future-less parked pending (record cleanup + session notices)
    pending_lifecycle.py terminate_pending - single terminal-path owner for pending ask_humans (pop + future settlement + Firebase cancel + benign-replay memory); ask arms, force-end, combine, session-end, spawn cleanup, and the TTL sweep all route through it
    bg_tasks.py          _BG_TASKS + _spawn_bg — strong-ref tracker for background tasks
scripts/
  install-service.ps1        One-time NSSM service install
  uninstall-service.ps1      Remove the service
  restart-service.ps1        Stop + pytest gate + start
  register-spawn-task.ps1    Re-register SwitchboardSpawn scheduled task
  spawn-launcher.ps1         Runs in user session to open a new terminal tab
  install-client.ps1         Build and deploy the Android app to a connected phone
  mod-typecheck.sh           Type-check the Claude Code mod against the engine's declarations
  deploy-mod.ps1             Refresh the desktop app's copy of the plugin (claude plugin update) and check it matches plugin/
plugin/                The Claude Code plugin: the marketplace's ./plugin source, and all the desktop app's copy holds
  .claude-plugin/
    plugin.json        Plugin manifest; no version, so each commit is a new version
  skills/
    switchboard/
      SKILL.md           Agent skill instructions (MCP tool signatures + Away Mode protocol)
  hooks/
    hooks.json           Names the Claude Code mod's module (switchboard.ts)
    switchboard.ts       The mod: every hook and every function that takes $ (server calls, inbox poll, delivery, stop, turn end, AskUserQuestion bridge, session start/end) plus the $.state atoms
    client.ts            Pure: base URL default, request headers, inbox-body parsing
    status.ts            Pure: activity-indicator mapping (tool state + detail)
    inbox.ts             Pure: poll interval and backoff
    turn-end.ts          Pure: away-mode turn-end decision + redirect text
    ask.ts               Pure: AskUserQuestion phone wording, reply unwrapping, terminal-sentinel check
    telemetry.ts         Pure: measurement report, effort level, latest title in a transcript, Claude config dir
    fingerprint.ts       Pure: the mod's content fingerprint (server/build_info.py mod_fingerprint computes the same)
    tests/               claude plugin test suites + harness.ts (the fake engine beneath the mod)
  types/
    index.d.ts           The mod's $.state contract
android/                     Two Gradle modules: app (phone UI), shared (library used by app). A paired Wear OS watch shows the phone's notifications through Wear OS bridging; there is no watch app
  shared/src/main/java/io/github/johnjanthony/switchboard/
    MainViewModel.kt         ALL Firebase RTDB listeners + command writers (StateFlow state)
    SessionBoardPolicy.kt    Pure sessions-board derivations (label chain, needs-attention, partition/sort, badge count)
    ConversationPolicy.kt    Pure conversation derivations (context rings, admin row, unread zeroing, picker targets)
    network/Models.kt        Data classes (@PropertyName annotated): ConversationSummary/Member/Row, RegistrySession, widget DTOs
  app/src/main/java/io/github/johnjanthony/switchboard/
    MainActivity.kt          NavHost (conversation list / chat / sessions board / markdown viewer) + dialog hoisting
    fcm/SwitchboardFirebaseMessagingService.kt  Push notifications (three channels, tap-to-conversation; a question's notification clears when the question ends)
    ui/                      Compose screens + composables (ConversationListScreen, SessionsBoardScreen, sheets, row composables)
    ui/theme/                Material3 dark "console" theme (Brass/Jade/Coral palette)
  app/build.gradle           Markwon, Firebase, Compose dependencies
watchtower/                  Windows client (.NET 9 / WinForms taskbar widget) — "Switchboard Watchtower"
  Switchboard.Watchtower.sln
  deploy-widget.ps1                  Rebuild + relaunch the widget: stop the running instance -> publish single-file EXE -> relaunch (-NoLaunch to skip relaunch). Full build/publish detail in README.md.
  src/Switchboard.Watchtower/        WinForms app (widget, hover popup, tray, Win32 taskbar placement, Claude status indicator, daily anchor); renders what GET /stats reports
  src/Switchboard.Watchtower.Core/   Pure logic (/stats parsing and the session adapter, quota pacing and format, window math, config, Claude status parse, the anchor's window check)
  tests/Switchboard.Watchtower.Core.Tests/   xUnit tests for the Core library
  tools/IconGen/                     One-off WinForms tool that renders the app icon (build helper)
dashboard/                  Switchboard Operator: zero-build Preact+htm web cockpit, served by the Python server at /dashboard
  index.html               Module shell + Firebase/Preact importmap
  dashboard-config.js      Public Firebase web config (committed; the real access control is the RTDB rules)
  schema.js                RTDB path builders (single source of path truth)
  firebase.js              Firebase Web SDK wrapper (Google auth + RTDB listeners/writes)
  derive.js                Pure derivations (member state, pending aggregation, oldest-pending age, composer pending sends)
  commands.js              Pure write-command builders, each returning {path, value}
  store.js                 Reactive view-model store (the single owner of projected state)
  markdown.js              Markdown renderer wrapping vendored markdown-it + highlight.js (GFM + syntax coloring, link-scheme validation)
  document.js              Document message pill + preview-page URL helpers (documentPillHtml)
  doc-view.js              Standalone document preview page (opened from a document pill)
  statusControl.js         Claude status control (POST /widget-status) + status-lamp color mapping
  app.js                   Boot wiring + /healthz poll + rollUpHealth (parity with /stats)
  components/              Preact+htm components: App, StatusBar, ConversationList, ConversationDetail, SessionsRail, PaneBanner
  vendor/                  Pinned Preact + hooks + htm ESM + the htm-preact binding
  styles.css               3-pane grid (independently collapsible rails)
  *.test.js                node --test units (schema, derive, commands, store, markdown)
logs/
  switchboard.jsonl    Runtime audit log (gitignored)
  sessions/            Per-conversation session transcript logs keyed by conversation_id (gitignored)
```

## Running locally

```bash
pip install -e ".[dev]"
# Either set FIREBASE_SERVICE_ACCOUNT_JSON and FIREBASE_DATABASE_URL as OS env vars,
# or create a .env file from .env.example and fill in the values.
python -m server
```

Gateway comes up on `http://127.0.0.1:9876`. Point your agent at `http://localhost:9876/mcp` (HTTP transport).

## Testing

```bash
pytest                 # all tests
pytest tests/test_registry.py -v
```

Integration tests run in-process; no external services required. The backends (Firebase, etc.) are mocked.

### Live smoke harness

`.venv\Scripts\python.exe scripts\smoke\smoke.py` drives the DEPLOYED service end-to-end: away-mode round-trip, at-desk redirect, live ask->answer->resolve, fastest-answer round-trip, message interjection, background ask, restart survival (parked-pending recovery), cleanup. Preflight first asserts the running service matches the working tree (the `/healthz` `service.source_fingerprint` against a hash of `server/**/*.py`) and fails with the restart command if not: a healthy-but-stale process otherwise passes flows it has no code for. It asserts, never restarts. Real Firebase, real service, real FCM — each run pings the phone 1-2 times and briefly toggles away mode; the default run RESTARTS the service (severs every live MCP session). Use `--skip-restart` when other agents are working; `--preflight-only` is read-only and always safe. The run leaves one hidden Ended conversation for the 72h retention sweep. Exit 0 = all flows passed.

## Building the Android app

The Android project lives entirely under `android/` — its own `settings.gradle`, wrapper, and `gradle.properties`. There is no Gradle build at the repo root.

```bash
cd android
./gradlew build
```

Requirements:

- **JDK** — the Gradle daemon is pinned to JetBrains JDK 21 by `android/gradle/gradle-daemon-jvm.properties` (auto-provisioned), so `JAVA_HOME` only launches the wrapper; Android Studio's bundled JBR (JDK 25) works.
- **`android/local.properties`** with `sdk.dir=...` pointing at your Android SDK. Gitignored — first-time setup only.
- **`android/app/google-services.json`** — Firebase config, gitignored. Download from the Firebase Console (Project Settings -> Your apps) for an app registered under this module's `applicationId`.
- **Android Studio**: open the `android/` directory (NOT the repo root) as the project.

**Installing on the phone.** `scripts/install-client.ps1` installs the debug build. For the release build run `.\gradlew.bat :app:installRelease` from `android/`: release is signed with the debug key (see `app/build.gradle`), so it installs over a debug install in place and Google sign-in keeps working. It is not a distribution signing setup.

**AV-induced first-build failures.** On Windows boxes with active on-access AV, the first build after a clean transforms cache can die with `Could not move temporary workspace ... AccessDeniedException` — the AV holds a handle on a freshly written jar while Gradle tries to atomic-rename its parent dir. Fix: re-run the build; by the second attempt the scan has finished. The failure only recurs after a cache wipe or Kotlin/AGP version bump.

## MCP tool surface

Active tools: `ask_human`, `notify_human`, `send_document_human`, `message_and_await_agent`, `post_agent_message`, `join_conversation`, `combine_conversations`, `lookup_conversation_ids`, `leave_conversation`, `set_away_mode`. Conversation tools return one-line JSON status envelopes (`ok | timeout | conversation_ended`); `ask_human` returns bare reply text with JSON terminal sentinels, `parked` among them when the session sweep judged the asking session gone while it waited (its question stays answerable). `ask_human` also takes `background=true`, which returns `{"status":"pending","request_id":...}` immediately instead of blocking: the question becomes a future-less pending in a second registry slot (so it never supersedes, and is never superseded by, the session's blocking ask), a second background ask appends to the same card, and John's answer is delivered later through `server/inbound.py`'s ladder. A pending background ask does NOT satisfy the away-mode turn-end hook; a live blocking ask (e.g. one the harness moved to a background task at ~120s) DOES.

Routing is by `cli_session_id`, injected by the plugin's Claude Code mod (a `tool.call` hook). Agents pass `sender` and tool-specific args only.

## Conversation model

Conversations are the persistence + routing unit. States: `Active` / `Ended`. A ref-less `join_conversation()` mints a new Active conversation, or lands the caller in the single still-solo conversation another agent minted ref-less within the last ~30 minutes (the candidate rule); zero or several candidates both mint a new room. An already-bound caller's ref-less join rejoins its bound conversation (the candidate rule applies only to unbound callers). Routing key is `cli_session_id` (hook-injected), not cwd. Away mode is a single global flag (`set_away_mode(bool)`). Ended conversations are retention-pruned from Firebase (index card + /messages + /answers, plus any Storage document blobs the messages reference) after 72h (SWITCHBOARD_CONVERSATION_RETENTION_HOURS); admin_notifications entries are pruned by the same hourly sweep after 168h (SWITCHBOARD_ADMIN_NOTIFICATION_RETENTION_HOURS); messages live at /messages/<conv_id>, answers at /answers/<conv_id>/<request_id>.

## Architectural constraints (decided)

- **Local gateway, cloud-synchronized state.** The compute is local; conversation/session/telemetry state persists and transits through Firebase (RTDB + FCM), which is a hard startup dependency. The founding "localhost only" principle was retired deliberately (2026-07-01 architecture review, D1); there is no Firebase-less run mode.
- **The service runs as LocalSystem** (NSSM; the installer's explicit default since the ops-hardening chunk - the earlier interactive-user intent never took effect and was removed; -ServiceUser overrides deliberately, verified post-set). It therefore cannot read `~/.claude/projects`, cannot reach WSL, and receives all session/telemetry data by push: the Claude Code mod's posts and MCP calls. Do not design features that assume the server can see John's files (D6).
- **Sessions are first-class** (2026-07-06): `server/session_registry.py` tracks every Claude Code session birth-to-death via the mod's session start, status, telemetry and session end posts, its 2 s inbox poll (the liveness heartbeat), and MCP calls, mirrored to RTDB `sessions/` for the roster surfaces. Identity is `cli_session_id` everywhere; `sender` is a display attribute (D4).

## Setup

Switchboard ships as a Claude Code plugin. From any Claude Code session:

```
/plugin marketplace add <path-to-this-repo>
/plugin install switchboard@switchboard
```

The plugin install wires the skill and the Claude Code mod (`plugin/`). Two things are installed separately:

1. **The MCP server connection.**

    ```bash
    # Windows
    claude mcp add switchboard --scope user --transport http http://localhost:9876/mcp

    # WSL (replace <windows-host-ip> with the value from `/etc/resolv.conf` or `ip route show default | awk '{print $3}'`)
    claude mcp add switchboard --scope user --transport http http://<windows-host-ip>:9876/mcp --header "Authorization: Bearer <SWITCHBOARD_TOKEN value>"
    ```

    WSL must use bridge networking (NOT mirrored). The Windows server requires `SWITCHBOARD_HOST=0.0.0.0` AND `SWITCHBOARD_TOKEN` set - the server refuses to start non-loopback without a token (REV-003 fail-closed), and every non-loopback client must send `Authorization: Bearer <token>` on all routes except `/healthz` (loopback callers are exempt). install-service.ps1 creates the inbound firewall rule (TCP 9876, scoped to the WSL NAT pool 172.16.0.0/12, rule name "Switchboard MCP (WSL)") as defense-in-depth; the token is the enforced control.

    For WSL agents, also point the mod at the Windows host so its HTTP callbacks don't fall back to `127.0.0.1` (unreachable from WSL). Export these in the WSL **login-shell** chain (`~/.profile` or `~/.bash_profile`, sourced directly), NOT only `~/.bashrc`: phone-spawned WSL agents launch via `wsl.exe -e bash -l` (a login, non-interactive shell) whose `~/.bashrc` early-returns at its interactive guard before reaching the var, so a `~/.bashrc`-only value never reaches a spawned agent and its `Bearer ${SWITCHBOARD_TOKEN}` header then expands empty (401):

    - `SWITCHBOARD_BASE_URL=http://<windows-host-ip>:9876` - read by the Claude Code mod (`plugin/hooks/switchboard.ts`).
    - `SWITCHBOARD_TOKEN=<same value as the server's .env>` - read by the same; sent as `Authorization: Bearer <token>`. Required for WSL agents once the server has a token; a wrong one shows as one `401` line in the session's transcript.

2. **The Python server (NSSM Windows service).** Install with `scripts/install-service.ps1`. The plugin's MCP connection is useless until this is running.

## Hooks

The Switchboard plugin's Claude Code hooks are a **mod**: TypeScript function hooks that run inside the Claude Code process (`plugin/hooks/hooks.json` names `plugin/hooks/switchboard.ts`; Claude Code 2.1.287 or later). Every function that takes the engine's `$`, and every `$.state` atom, lives in `plugin/hooks/switchboard.ts`, because the engine's load-time scan follows `$` and state references only within the module `hooks.json` names; a mod that passes `$` into an imported function does not load. The pure logic sits beside it: `client.ts` (base URL, headers, inbox parsing), `status.ts` (the activity-indicator mapping), `inbox.ts` (poll timing and backoff), `turn-end.ts` (the away-mode turn-end decision and redirect text), `ask.ts` (phone wording and reply parsing), `telemetry.ts` (telemetry reports and the transcript title). The `$.state` contract is `plugin/types/index.d.ts`, with every member named inline in `PluginState` (the validator does not follow a type alias).

- **Injector:** a `tool.call` hook adds `cli_session_id` and `cwd` to every `mcp__switchboard__*` call. The mod does not pre-approve the tools (see the security-plugin note below); John's sessions run in `bypassPermissions`, and elsewhere an `mcp__switchboard__*` allow rule does it.
- **Status:** `tool.call` posts the tool state before a call and `thinking` after; `turn.start` posts `thinking`; the turn end posts `clear`. Fire-and-forget POSTs to `/agent_status`.
- **Inbox:** every interactive session, and every desktop-app session once the app attaches as a surface (`session.attach`; after a hot reload, a surface already attached at `session.start`), polls `GET /sessions/{sid}/inbox` every 2 s (a `-p` run never polls), the only route that pops a Claude Code session's notices. Idle, held phone messages are submitted as a prompt (a turn starts with nobody at the terminal); busy, they ride the next main-loop tool result, a typed prompt, or the turn-end prompt. At the desk a draft in the prompt box defers delivery; a desktop-app session has no such check, since the engine cannot see the app's input box. A stop (`POST /sessions/{sid}/stop`) cancels the running turn. Each poll is also the session's liveness heartbeat: the sweep marks a polling session lost after 90 s without one (`SWITCHBOARD_SESSION_POLL_LOST_AFTER_SECONDS`), after a grace period of the same length following a start or a sleep, and a later poll revives it.
- **Telemetry:** `session.measure` posts the context (`tokens`, `window`) when it moved and, once a model request has run since they last went, the rate limits (moved or not, so the server can confirm the quota reading; a measurement before the first request, or after `/clear`, sends none), and a main-thread `turn.step` posts `model` and `effort` when they change, to `POST /sessions/{sid}/telemetry` (fire-and-forget, no retry; the server writes the session record and the account-wide `quota` node). The title comes from the transcript: read at `session.start` and after each main-loop turn until one is found, and again after `/rename` (a `command.run` hook). `$.fs.read` refuses a file over 4 MiB, so a refused read ends the search for that session and a `/rename` falls back to its args. State is keyed by session id, so a `/clear` reports afresh.
- **Mod fingerprint:** once per session id the mod sends `{mod_fingerprint}` to the telemetry route: SHA-256 over its own top-level `hooks/*.ts` (`plugin/hooks/fingerprint.ts`). The server compares it with the repo's `plugin/hooks` (`server/build_info.py` `mod_fingerprint`, re-read every sweep tick) and sets `mod_stale` on the session record; `/healthz` `mod.stale_sessions` lists live sessions on an older mod and Operator shows them an "old mod" chip. A session on a mod older than this check reports nothing and shows null, not stale.
- **Turn end:** on a main-loop `turn.complete` that ended normally, the mod reads the inbox; in away mode without a live blocking ask it submits the redirect text (after any held messages) as the agent's next prompt, the job the Python Stop hook did with a block. A live blocking ask lets the turn end quietly: the harness backgrounds MCP calls still running at ~120s, so a turn can legitimately end while its `ask_human` awaits John (without this, the redirect induced a re-ask that superseded the live question every ~2 minutes). Parked (future-less) and background asks do not count. At the desk, held messages are delivered as on any idle poll. An interrupted turn gets no check.
- **AskUserQuestion:** in away mode each question goes to the phone through `ask_human` (called with `$.mcp.call`; option labels become suggestions) and the replies return as the tool's answers. `$.mcp.call` hands back each switchboard reply wrapped as `{"result": "<reply>"}`, which `ask.ts` unwraps. Claude Code moves the mod's own `$.mcp.call` to a background task at ~120 s exactly as it does the model's MCP calls, handing back its "moved to the background as task <id>" text in place of the reply; the bridge recognises it (`backgroundedTaskId`, wording pinned by a test), asks no further questions, and denies with a reason saying John's answer will arrive as that task's result. Moving the bridge off MCP is a backlog option.
- **Session start / end:** `session.start` POSTs `/session_start` (no `source`; `/clear` raises no `session.start`, so a cleared id is registered by its first inbox poll, with an empty cwd until its first status post); `session.end` POSTs `/session_end` inside Claude Code's 1.5 s exit bound. Every server call is bounded at 1.5 s (`FETCH_TIMEOUT_MS`), because `$.http.fetch` has no timeout.

Develop the mod with `claude plugin test plugin` (tests in `plugin/hooks/tests/`; `harness.ts` is the fake engine beneath the mod), `claude plugin validate plugin/.claude-plugin/plugin.json` (validating a folder checks only a `marketplace.json` in it and never loads the hooks module), and `bash scripts/mod-typecheck.sh`; `tests/test_mod_plugin.py` runs the first two from pytest so a Claude Code update that breaks the early-access API fails the suite. **The org's security plugin bypasses a user mod's `classic.*` and `tool.check` hooks** (`cc-plugin-sec-default`; the debug log reads `switchboard: classic.Stop bypassed by cc-plugin-sec-default (tier user); beneath runs`). `claude plugin test` does not load that plugin, so such a hook passes its tests and never runs in a session: build on `turn.*`, `session.*`, `tool.call` and `prompt.submit`, and check a new event with `claude -p --debug-file`.

**Sessions load the plugin from the live repo.** The marketplace is a `directory` source at this repo whose plugin is the `plugin/` folder: a new terminal session reads `plugin/hooks/hooks.json` and the mod straight from the working tree (a version bump does not gate it), and `${CLAUDE_PLUGIN_ROOT}` in a running session's hook commands expands to the working tree, not the version-gated cache. A mod edit on disk, committed or not, reaches the next session opened; deploy the server first when the two change together. Deleting or renaming a script that some session's loaded `hooks.json` still calls breaks that session at once (a Python "can't open file" exits 2, which blocks every PreToolUse). Remove a hook script only after every session has relaunched onto hooks that no longer call it. WSL is a separate install: its marketplace is its own clone (`~/work/switchboard`, per `~/.claude/plugins/known_marketplaces.json` there), so WSL sessions pick up a change only after a `git pull --ff-only` in that clone, and the same relaunch rule applies to WSL sessions before a pull that deletes a script.

**Desktop sessions run a copy.** The Claude for Windows desktop app passes each installed plugin to Claude Code by its `installPath` in `~/.claude/plugins/installed_plugins.json`, a copy under `~/.claude/plugins/cache/`, so its sessions load the mod as `switchboard@inline` from that copy, not from the working tree (a terminal session's debug log says `switchboard@switchboard`). `claude plugin update` refreshes the copy only when the plugin's version changes, and `plugin/.claude-plugin/plugin.json` has no `version`, so the version is the commit. After a commit that touches `plugin/`, run `scripts/deploy-mod.ps1`: it refuses an uncommitted `plugin/`, runs the update and checks the copy matches. New desktop sessions load the new copy; running ones keep theirs until restarted, and `/healthz` `mod.stale_sessions` lists them.

**Server-side gating.** The server's `/agent_status` handler skips the Firebase conversation-status write when away mode is off, so the phone status indicator is only visible during away mode. The route upserts the SessionRegistry before that gate, so the session roster always updates. The HTTP layer always returns 200.

## Service management (Windows service via NSSM)

The server runs as a Windows service so it starts automatically and survives the terminal closing.

```powershell
# One-time install (elevated PowerShell):
choco install nssm          # install NSSM via Chocolatey
.\scripts\install-service.ps1

# Check status:
nssm status switchboard

# Restart after code changes — stops service, runs pytest gate, restarts:
.\scripts\restart-service.ps1 -SkipTests   # ALWAYS use -SkipTests when running as an agent
.\scripts\restart-service.ps1              # human-initiated restarts may omit -SkipTests to run the gate

# Re-register the SwitchboardSpawn scheduled task (if missing or after re-install):
.\scripts\register-spawn-task.ps1      # elevated PowerShell

# Remove the service:
.\scripts\uninstall-service.ps1  # elevated PowerShell
```

**Agents rebuilding or restarting the service MUST use `-SkipTests`.** The pytest gate takes ~15 seconds, which consumes the auto-reconnect window (31 seconds) and causes the MCP connection to drop permanently. With `-SkipTests`, the service restarts in ~3 seconds and agents auto-reconnect within the window.

Logs: `logs\switchboard.jsonl` (JSONL audit), `logs\nssm-stdout.log` / `nssm-stderr.log` (uvicorn console). NSSM sets `AppDirectory` to the repo root so `config.py`'s `.env` fallback resolves correctly.

**Diagnostic:** `curl -s http://localhost:9876/healthz | python -m json.tool` reports listener supervision state, dispatch-loop crash counts, pending-question state, what the process is running (`service`: start time, source fingerprint, git HEAD), and which live sessions run an older mod than the repo (`mod`: `repo_fingerprint`, `stale_sessions`). `smoke.py --preflight-only` is the read-only way to ask "is the deployed service current?".

## Away Mode Protocol

**CRITICAL: When away mode is active, DO NOT PRODUCE ANY TEXT RESPONSE IN THE TERMINAL.**

The moment the operator says they are stepping away (or any similar phrasing), switch all output to the `notify_human` and `ask_human` tools. Any terminal output after this point is a failure.

**Activation:**

- If tasks are queued: `notify_human` to confirm you have entered away mode and are starting work, then proceed.
- If idle: `ask_human` to ask what's next.
- If mid-task: `notify_human` to report status, then `ask_human` for next steps.

**The tool call IS the acknowledgment.** Do not type "Got it" or "Okay" in the terminal first.

**Execution:**

- Route **every** subsequent output (status, questions, completion) through `ask_human`, `notify_human`, or `send_document_human`.
- Receiving a reply to `ask_human` **does not** exit away mode. Do not respond to replies in the terminal.
- The built-in `AskUserQuestion` tool works in away mode: the plugin's mod sends each question to John's phone through `ask_human` and returns his replies as the answers. If the call is denied, its reason says why the phone could not answer; continue from it, re-asking through `ask_human` if you still need the answer.

**Exit:**

- Only exit when the operator explicitly says they are back ("I'm back", "back at desk").
- When you resume terminal interaction, give a concise summary of what you did while away.

**MCP transport:**

- Server uses stateful HTTP (`stateless_http=False`). This is what makes per-tool-call `notifications/cancelled` propagate from the CLI to the in-flight responder so a cancel from the terminal correctly marks the question `cancelled: true`.
- Cost: a server restart momentarily interrupts every active MCP session. Claude Code re-establishes its session on its own after the restart (observed 2026-07-24); if a session does not recover, `/exit` + relaunch restores the tools. Away mode is NOT affected by a restart - it persists across restarts (T-029) and is toggled only from the phone/dashboard (or the `set_away_mode` tool), so a pre-restart agent resumes with away mode intact and keeps routing through the switchboard tools.
## Recovery when the turn-end hook blocks and MCP tools are unavailable

If the Switchboard MCP tools disconnect from your session while the away-mode flag is active, the turn-end hook will block every response with no way to call `set_away_mode(false)` or `notify_human`. Symptom: every turn ends with "Stop hook feedback: You are in away mode..." and `mcp__switchboard__*` tools are gone. Recovery, in order of preference:

1. **Toggle via the Android global pill chip (Page A top-bar)** — long-press -> Exit. Writes `global_settings/away_mode = false` to Firebase; the hook reads the server state and stops blocking.
2. **Restart the service** (`.\scripts\restart-service.ps1 -SkipTests`) — Claude Code reconnects its MCP tools afterward, so use this if the tools are genuinely wedged. Note: a restart no longer turns away mode off (it persists across restarts, T-029), so it is NOT a way to exit away mode — use the phone toggle (option 1) for that.
3. **Inspect `/healthz`** — reports per-listener state (`live` / `reconnecting` / `starting` / `stopped`), per-loop crash counts, and pending-question state. A `reconnecting` listener means the supervisor detected its SDK thread died and is rebuilding with exponential backoff — wait ~5 s and retry.

## Spawn flow

When the user taps "+" on the phone, they choose surface (Windows / WSL), project, optional prompt, and whether to create a new conversation or add the spawned agent into an existing one. The server dispatches via structured Firebase `/spawn_commands/` entries; `SpawnHandler.handle_fresh` and `handle_resume` are the two entry points. Spawn auto-enables away mode if currently off.

The fresh-spawn dialogs (phone and Operator) also offer **Model** and **Effort** pickers, populated from the server-published `spawn_options/` catalog that `server/spawn_catalog.py` builds; blank means no flag and the CLI's own default. Invalid picks are rejected loudly at dispatch (`send_text` to the phone plus an audit entry, no tab opened) because Claude Code cannot be trusted to reject them: it silently ignores a bad `--effort`, and real launch errors die inside the spawned `wt` tab where the phone never sees them. A pick is recorded on the session (`SessionRecord.spawn_model` / `spawn_effort`, distinct from the ring-observed `model`) and re-passed by every resume path, since `claude --resume` preserves the model but resets effort; a recorded value that no longer validates is dropped fail-soft with a phone notice rather than blocking the resume. A spawn that names another agent, or a resume of a record another CLI wrote, is rejected the same way.

**Spawn-root env vars** (both consumed by `config.py`):

- `SWITCHBOARD_WINDOWS_SPAWN_ROOT` — Windows root containing project folders (e.g. `C:\Work`). Legacy alias `SWITCHBOARD_SPAWN_ROOT` is still accepted.
- `SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT` — segment appended to the resolved WSL home to locate the WSL workspace root. Default `work`. The WSL surface is rejected at spawn time if the WSL home is unresolved.

`handle_fresh` and `handle_resume` call `_user_has_interactive_session()` (runs `quser`) before triggering the scheduled task and abort with a "Cannot spawn: no one is logged in..." message if no `Active` / `Disc` session exists. Without this, `schtasks /run` returns success even when there's no interactive desktop session for it to launch `wt` into. The gate degrades open if `quser` fails to launch.

## List-based UI

The Android client uses a list-based two-page nav:

- **Page A**: conversation list, ordered by last activity. Each row shows the title, relative timestamp, unseen-activity dot, AWAY badge (when away mode is on), and the open-conversation accent border + "open" label. Swipe-right to end; swipe-left to hide. Long-press for a context menu: Resume, Combine into..., Hide/Unhide, End conversation. The Resume item is enabled when any of the conversation's member sessions has a terminal (ended/lost) registry record.
- **Page B**: per-conversation message view with the tab info popover and a reply input (visible only when there's a pending question).

Hidden conversations are accessible via the overflow menu's "Show hidden" toggle. FCM notification taps deep-link directly to Page B.

## Conventions

- **Python 3.11+, asyncio end-to-end.** No threads, no `run_in_executor` unless a blocking library forces it.
- **Tool handlers stay thin.** `ask_human` in `gateway/handlers.py` should create the pending record, broadcast it to surfaces, and `await future` — nothing else. Complexity lives in the per-surface modules.
- **Single mandatory backend.** Firebase is required; the server exits at startup with a ConfigError if its env vars are unset. Optional sub-features degrade gracefully (document delivery needs `FIREBASE_STORAGE_BUCKET`; spawn needs the spawn-root vars), but the core gateway does not start without Firebase.
- **Conversations persist; in-flight futures don't.** Conversation state persists in Firebase and rehydrates on restart (`server/hydration.py`); pending `ask_human` futures and wait queues are in-memory and do not survive restart. Don't add a second datastore without a design revision.
- **Comments sparingly.** Explain why, not what.

## Git policy

John's global CLAUDE.md Git tiers apply: `add` / `commit` are allowed subject to the Branch Check; `push` and PR creation only at John's explicit direction; everything else (merge, rebase, reset, checkout/switch, branch creation) is prohibited.

**This repo opts in to direct commits on `develop`.** Work here happens on `develop`, so the Branch Check's protected-branch rule does not block committing there. The opt-in does not extend to `main` or any other protected line, and every other step of the Branch Check still runs: confirm the branch fresh, confirm no merge or rebase is in progress, and never sweep changes you did not make into your commit.

## Knowledge graph & Obsidian vault

The repo has a Graphify knowledge graph (`graphify-out/graph.json`, gitignored, rebuilt automatically by post-commit/post-checkout git hooks — AST-only, no LLM cost).

- **Query it before grepping** for architecture / what-calls-what / data-flow questions: `graphify query "<question>"`, `graphify explain "<node>"`, `graphify path "A" "B"`. Human-readable map: `graphify-out/GRAPH_REPORT.md`; interactive: `graphify-out/graph.html`.
- **Architecture questions: use the production-only view.** Test code is ~52% of the nodes and ~65% of the edges in the full graph, so default queries drown in test scaffolding. Append `--graph graphify-out/graph-src.json` to `query` / `explain` / `path` / `affected` for architecture questions; use the full `graph.json` only when tests are the point (e.g. "which tests cover X"). The view is derived by `scripts/graphify-src-view.py`; the git hooks refresh it after each rebuild. If `graph-src.json` is older than `graph.json` (e.g. after `graphify hook install` re-writes the hooks and drops the refresh step), regenerate it: `python scripts/graphify-src-view.py`.
- **Edge verdicts** (`.graphify-verdicts.json`, repo root, git-tracked): confirm/reject records for INFERRED/AMBIGUOUS edges, keyed by (source, target, relation). `scripts/graphify-src-view.py` re-applies it on every rebuild, after the dup-merge/junk-filter and before community recompute - rejected edges are dropped from `graph-src.json`, confirmed edges flip to EXTRACTED with `verified` provenance; `graph.json` itself is never touched. The src-view summary line reports confirmed/rejected/stale/unverdicted counts; stale verdicts (matching no edge) are warned and kept, and new shaky edges from later commits sit unverdicted until the next sweep.
- **The doc layer ages silently.** The hooks rebuild code only (AST, no LLM), so new design docs/specs/images never enter the graph on their own - the concept/rationale/document notes are frozen at the last semantic extraction. Periodic fix (quarterly or after a doc wave): run the semantic re-extraction runbook in the main vault's Graphify Usage note (`graphify extract` incremental + view/vault refresh + mini-sweep of the new unverdicted count).
- **Obsidian vault** at `graphify-out/obsidian/` is a generated view of the **production-only** graph (`graph-src.json` - no test notes) - entry point `Start Here.md`; `Flow Index.md` / `Rationale Index.md` enumerate the flow hyperedges and rationale notes; shaky-extraction review in `INFERRED Review.base`. Notes carry a source excerpt and a greppable `Source: <path>:<line>` line - for what-is-X / what-does-X-do questions, read the note before opening source. The git hooks re-run hygiene + enrichment on every commit; `refresh-obsidian-vault.py` warns loudly when `graph-src.json` is stale.
- **Use the vault when the task benefits from it:** grounding in an unfamiliar subsystem (start at `Start Here.md`, then the indexes), structural questions (what calls X, what shares data with Y, which design docs shaped Z), locating the rationale behind a design decision, and design-spec reviews that need the component map. Plain grep remains better for exact-string hunts and exhaustive edge-case sweeps.
- **Obsidian app dependency:** the `obsidian` CLI (obsidian-cli skill) needs a RUNNING Obsidian instance and hangs indefinitely without one (no error, no timeout). If a task wants Obsidian tooling (vault search, backlinks, dev commands) and the app is not running, STOP and ask John to start it - do not fall back silently and do not leave a hung command in the background. Reading vault files directly with Read/Grep needs no running app and is always safe.
- **Re-exporting the vault**: `python scripts/refresh-obsidian-vault.py` — the one canonical pipeline (export from `graph-src.json` + dedot + ownership-manifest sync). Do NOT run bare `graphify export obsidian` into this vault: the raw exporter crashes partway on control chars in node labels (a tab makes an invalid Windows filename; this is what produced a silent 665-note partial vault on 2026-07-18), and without the manifest sync every re-export accumulates duplicate " (method)" notes. `scripts/dedot-obsidian.ps1` remains for ad-hoc exports elsewhere; the dedot logic also lives inside the refresh script.
- **`.graphifyignore` is load-bearing** (excludes `dashboard/vendor/`); without it a rebuild regenerates ~300 junk nodes from vendored JS.
- Don't commit `graphify-out/` — regenerable output, and the post-commit hook would dirty the tree on every commit if tracked.
- Vault conventions and the full Graphify lesson list live in the main knowledge vault: `C:\Work\ClaudeObsidian\Claude Vault\Reference\Graphify Usage.md`.
