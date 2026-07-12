# Switchboard — Agent Orientation

Switchboard is the mission-control hub for a multi-agent workstation: it tracks Claude Code sessions and conversations, routes questions and notifications to a phone, mirrors telemetry to ambient surfaces (Operator, Watchtower), and dispatches phone-issued commands (spawn, combine, away mode). Its founding feature - and still the core protocol - is away mode: agents pause mid-task and request human input, answered from the phone.

This file orients any agent (Claude Code, etc.) working **on Switchboard itself** or **consuming** its tools. Switchboard ships as a Claude Code plugin; see [Setup](#setup) below.

Design history lives under [`docs/`](docs/); the current end-to-end design is [`docs/switchboard-design-spec-comprehensive.md`](docs/switchboard-design-spec-comprehensive.md), and the dated specs under [`docs/superpowers/specs/`](docs/superpowers/specs/) record how each subsystem evolved. The newest dated spec wins where two overlap.

## Project shape

Single Python process, one asyncio event loop, MCP HTTP server on `localhost:9876`, with a Firebase backend (Android + Realtime Database). The server also serves **Switchboard Operator**, a launch-on-demand web cockpit (a zero-build Preact+htm app in `dashboard/`), at `/dashboard`, plus a small widget-facing `GET /stats` roll-up. The dashboard talks to Firebase RTDB directly and reads `/healthz` for its health panel.

Away mode is the founding feature, not the whole product: ask/notify blocking semantics are away-mode-scoped (at-desk interaction uses the terminal), while session tracking, telemetry fan-out, Operator, and Watchtower are always-on ambient surfaces.

The Registry is in-memory. The pending-request index is keyed by `(conversation_id, cli_session_id)` tuples where `conversation_id` is a `conv-<uuid>` string from `Registry.conversations` — not a filesystem path; answers resolve by `(conversation_id, request_id)`. Pending ask_human futures die on restart, but the questions survive: hydration rebuilds pending_questions records as parked (future-less) pendings, an arriving answer resolves them with a history write plus a session notice, and unanswered ones expire at the 72h retention horizon (chunk 7). Conversations (the persistence unit) survive restart via Firebase hydration — see `server/hydration.py`.

## Layout

```text
server/
  __init__.py          Package marker
  __main__.py          Enables `python -m server`
  main.py              Entry point — wires config, registry, backend, MCP, uvicorn
  http_auth.py         TokenAuthMiddleware - shared-secret Bearer gate (loopback peers and /healthz exempt; active when SWITCHBOARD_TOKEN is set)
  config.py            Env-based Config loader (dotenv fallback)
  registry.py          PendingRequest + Registry (in-memory); conversations dict with members/pendings keyed by cli_session_id; session_to_conversation_id routing map; per-session asyncio.Lock for race-free first-call conv creation; global away-mode flag
  session_registry.py  SessionRecord + SessionRegistry (session roster; push-fed; sweeper rules)
  messenger.py         Backend lifecycle base + 4 trait ABCs (MessageWriter, ResponsePoller, AwayModeMirror, ChannelLifecycle) + ConversationStore protocol + IncomingResponse
  firebase.py          FirebaseBackend (implements every messenger surface); Firebase admin logic (FCM, Realtime DB)
  spawn.py             Agent session spawner (triggered from Android app)
  conversation_ops.py  Conversation lifecycle helpers (create, add/migrate member, queue-for-intro, wake, combine, session-fallback); sender-collision auto-disambiguation ('Claude Win' -> 'Claude Win 2' etc.)
  cli_session_end.py   handle_session_end: marks a member dormant on session end; invoked by the marker-file sweep (dispatch_session_end_markers)
  rate_limiter.py      Per-channel token-bucket rate limiter for notify_human and send_document_human
  canonicalization.py  Canonical-cwd normalization (display-only; cwd is a display tag)
  logging_jsonl.py     JSONL audit log
  hydration.py         Rebuilds Registry state from Firebase on startup (conversations survive restart)
  rules_audit.py       Startup audit of the deployed RTDB rules (placeholder/test-mode detection; loud, non-fatal)
  firebase_supervisor.py  SupervisedListener + LoopSupervisor (Firebase-listener / dispatch-loop supervision for /healthz)
  session_fallback.py  Session-to-conversation fallback resolution (home-conversation rebind / unbind)
  command_freshness.py Staleness gate for queued Firebase command entries (COMMAND_TTL_SECONDS)
  claude_status.py     Claude service-status watch (poll loop + status parse published to widget/status)
  widget_snapshot.py   WidgetSnapshotStore for the /widget-snapshot POST payload (canonical de-dup)
  gateway/             Tool handlers + dispatch loops
    handlers.py          ask_human, notify_human, send_document_human, message_and_await_agent, join_conversation, combine_conversations, lookup_conversation_ids, leave_conversation, set_away_mode tool closures; JSON status envelopes (_envelope/_terminal_envelope/_wrap_wait_result)
    dispatch.py          dispatch_responses, dispatch_combine_commands, dispatch_force_end_commands, dispatch_spawn_commands, dispatch_away_mode_commands, dispatch_status_request_commands, dispatch_session_end_markers, dispatch_session_sweep, dispatch_conversation_sweep, handle_force_end
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
skills/
  switchboard/
    SKILL.md           Agent skill instructions (MCP tool signatures + Away Mode protocol)
android/                     Three Gradle modules: app (phone UI), shared (library used by app + wear), wear (watch)
  shared/src/main/java/io/github/johnjanthony/switchboard/
    MainViewModel.kt         ALL Firebase RTDB listeners + command writers (StateFlow state; shared by app and wear)
    SessionBoardPolicy.kt    Pure sessions-board derivations (label chain, needs-attention, partition/sort, badge count)
    ConversationPolicy.kt    Pure conversation derivations (context rings, watch partition)
    network/Models.kt        Data classes (@PropertyName annotated): ConversationSummary/Member/Row, RegistrySession, widget DTOs
  app/src/main/java/io/github/johnjanthony/switchboard/
    MainActivity.kt          NavHost (conversation list / chat / sessions board / markdown viewer) + dialog hoisting
    fcm/SwitchboardFirebaseMessagingService.kt  Push notifications (three channels, tap-to-conversation)
    ui/                      Compose screens + composables (ConversationListScreen, SessionsBoardScreen, sheets, row composables)
    ui/theme/                Material3 dark "console" theme (Brass/Jade/Coral palette)
  wear/                      Wear OS companion (own Compose UI; consumes the shared MainViewModel and models)
  app/build.gradle           Markwon, Firebase, Compose dependencies
watchtower/                  Windows client (.NET 9 / WinForms taskbar widget) — "Switchboard Watchtower"
  Switchboard.Watchtower.sln
  src/Switchboard.Watchtower/        WinForms app (widget, hover popup, tray, Win32 taskbar placement, Claude status indicator)
  src/Switchboard.Watchtower.Core/   Pure logic (transcript parsing, session scanners, quota, window math, config, Claude status parse; the status watch state machine lives server-side)
  tests/Switchboard.Watchtower.Core.Tests/   xUnit tests for the Core library
  tools/IconGen/                     One-off WinForms tool that renders the app icon (build helper)
dashboard/                  Switchboard Operator: zero-build Preact+htm web cockpit, served by the Python server at /dashboard
  index.html               Module shell + Firebase/Preact importmap
  dashboard-config.js      Public Firebase web config (committed; the real access control is the RTDB rules)
  schema.js                RTDB path builders (single source of path truth)
  firebase.js              Firebase Web SDK wrapper (Google auth + RTDB listeners/writes)
  derive.js                Pure derivations (member state, pending aggregation, oldest-pending age)
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

## Building the Android app

The Android project lives entirely under `android/` — its own `settings.gradle`, wrapper, and `gradle.properties`. There is no Gradle build at the repo root.

```bash
cd android
./gradlew build
```

Requirements:

- **JDK 21** — AGP 9.x will not run on 17. Android Studio's bundled JBR is an easy source; set `JAVA_HOME` to it for CLI builds.
- **`android/local.properties`** with `sdk.dir=...` pointing at your Android SDK. Gitignored — first-time setup only.
- **`android/app/google-services.json`** — Firebase config, gitignored. Download from the Firebase Console (Project Settings -> Your apps) for an app registered under this module's `applicationId`.
- **Android Studio**: open the `android/` directory (NOT the repo root) as the project.

**AV-induced first-build failures.** On Windows boxes with active on-access AV, the first build after a clean transforms cache can die with `Could not move temporary workspace ... AccessDeniedException` — the AV holds a handle on a freshly written jar while Gradle tries to atomic-rename its parent dir. Fix: re-run the build; by the second attempt the scan has finished. The failure only recurs after a cache wipe or Kotlin/AGP version bump.

## MCP tool surface

Active tools: `ask_human`, `notify_human`, `send_document_human`, `message_and_await_agent`, `join_conversation`, `combine_conversations`, `lookup_conversation_ids`, `leave_conversation`, `set_away_mode`. Conversation tools return one-line JSON status envelopes (`ok | timeout | conversation_ended`); `ask_human` returns bare reply text with JSON terminal sentinels.

Routing is by `cli_session_id`, injected by the `cli-session-injector-hook.py` PreToolUse hook. Agents pass `sender` and tool-specific args only.

## Conversation model

Conversations are the persistence + routing unit. States: `Active` / `Ended`. A ref-less `join_conversation()` mints a new Active conversation, or lands the caller in the single still-solo conversation another agent minted ref-less within the last ~30 minutes (the candidate rule); zero or several candidates both mint a new room. An already-bound caller's ref-less join rejoins its bound conversation (the candidate rule applies only to unbound callers). Routing key is `cli_session_id` (hook-injected), not cwd. Away mode is a single global flag (`set_away_mode(bool)`). Ended conversations are retention-pruned from Firebase (index card + /messages + /answers) after 72h (SWITCHBOARD_CONVERSATION_RETENTION_HOURS); messages live at /messages/<conv_id>, answers at /answers/<conv_id>/<request_id>.

## Architectural constraints (decided)

- **Local gateway, cloud-synchronized state.** The compute is local; conversation/session/telemetry state persists and transits through Firebase (RTDB + FCM), which is a hard startup dependency. The founding "localhost only" principle was retired deliberately (2026-07-01 architecture review, D1); there is no Firebase-less run mode.
- **The service runs as LocalSystem** (NSSM; the install script's interactive-user intent never took effect, verified 2026-06-25). It therefore cannot read `~/.claude/projects`, cannot reach WSL, and receives all session/telemetry data by push: plugin hooks, Watchtower snapshots, MCP calls. Do not design features that assume the server can see John's files (D6).
- **Sessions are first-class** (2026-07-06): `server/session_registry.py` tracks every Claude Code session birth-to-death via SessionStart/agent-status/SessionEnd hooks, ring sightings, and MCP calls, mirrored to RTDB `sessions/` for the roster surfaces. Identity is `cli_session_id` everywhere; `sender` is a display attribute (D4).

## Setup

Switchboard ships as a Claude Code plugin. From any Claude Code session:

```
/plugin marketplace add <path-to-this-repo>
/plugin install switchboard@switchboard
```

The plugin install wires the skill and the turn-end + agent-status hooks. Two things are installed separately:

1. **The MCP server connection.**

    ```bash
    # Windows
    claude mcp add switchboard --scope user --transport http http://localhost:9876/mcp

    # WSL (replace <windows-host-ip> with the value from `/etc/resolv.conf` or `ip route show default | awk '{print $3}'`)
    claude mcp add switchboard --scope user --transport http http://<windows-host-ip>:9876/mcp --header "Authorization: Bearer <SWITCHBOARD_TOKEN value>"
    ```

    WSL must use bridge networking (NOT mirrored). The Windows server requires `SWITCHBOARD_HOST=0.0.0.0` AND `SWITCHBOARD_TOKEN` set - the server refuses to start non-loopback without a token (REV-003 fail-closed), and every non-loopback client must send `Authorization: Bearer <token>` on all routes except `/healthz` (loopback callers are exempt). The firewall inbound rule for TCP 9876 from the WSL subnet remains recommended as defense-in-depth; the token is the enforced control.

    For WSL agents, also point the hook scripts at the Windows host so their HTTP callbacks don't fall back to `127.0.0.1` (unreachable from WSL). Export these in the WSL **login-shell** chain (`~/.profile` or `~/.bash_profile`, sourced directly), NOT only `~/.bashrc`: phone-spawned WSL agents launch via `wsl.exe -e bash -l` (a login, non-interactive shell) whose `~/.bashrc` early-returns at its interactive guard before reaching the var, so a `~/.bashrc`-only value never reaches a spawned agent and its `Bearer ${SWITCHBOARD_TOKEN}` header then expands empty (401):

    - `SWITCHBOARD_BASE_URL=http://<windows-host-ip>:9876` - read by the three HTTP hooks (`agent-status-hook.py` POSTs to `/agent_status`; `turn-end-hook-away-mode.py` GETs `/away-mode`; `cli-session-start-hook.py` POSTs to `/session_start`).
    - `SWITCHBOARD_TOKEN=<same value as the server's .env>` - read by the same three hooks; they attach `Authorization: Bearer <token>` when it is set. Required for WSL agents once the server has a token.
    - `SWITCHBOARD_MARKER_DIR=<path>` - read by `cli-session-end-hook.py`, which writes a SessionEnd marker FILE (not an HTTP POST) that the server sweeps; point it at the server's `<logs>/session-end` dir when the hook runs on a different host.

2. **The Python server (NSSM Windows service).** Install with `scripts/install-service.ps1`. The plugin's MCP connection is useless until this is running.

## Hooks

The Switchboard plugin wires six Claude Code hook events automatically:

- `Stop` (two handlers) — `turn-end-hook-away-mode.py` for the away-mode enforcement check; `agent-status-hook.py` for the per-conversation activity indicator.
- `UserPromptSubmit`, `PreToolUse`, `PostToolUse` — `agent-status-hook.py` for the activity indicator. `PreToolUse` also runs `cli-session-injector-hook.py`, which injects `cli_session_id` + `cwd` into every `mcp__switchboard__*` call.
- `SessionStart` — `cli-session-start-hook.py` POSTs the session's birth (`session_id`, `cwd`, `source`) to the server's `/session_start` route so the SessionRegistry records the session; a missed birth self-heals on the first MCP call or agent-status event.
- `SessionEnd` — `cli-session-end-hook.py` writes a SessionEnd marker file (under `SWITCHBOARD_MARKER_DIR`, the server's `<logs>/session-end` dir) that the server's `dispatch_session_end_markers` sweep applies to mark the session's member dormant on orderly exit (the marker write wins the process-exit race a synchronous POST loses).

See `hooks/hooks.json` for the canonical wiring.

**Server-side gating.** Hooks fire on every lifecycle event regardless of away-mode state, but the server's `/agent_status` handler short-circuits and skips the Firebase write when the cwd is not in away mode. The phone status indicator is therefore only visible during away mode. The `/agent_status` route upserts the SessionRegistry before that away-mode gate, so the session roster always updates even when the phone-facing conversation-status write is skipped; only the phone status indicator is away-mode-gated. The HTTP layer always returns 200 so the hook contract is unchanged; the gate is invisible to the hook script.

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

**Diagnostic:** `curl -s http://localhost:9876/healthz | python -m json.tool` reports listener supervision state, dispatch-loop crash counts, and pending-question state.

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

**Exit:**

- Only exit when the operator explicitly says they are back ("I'm back", "back at desk").
- When you resume terminal interaction, give a concise summary of what you did while away.

**MCP transport:**

- Server uses stateful HTTP (`stateless_http=False`). This is what makes per-tool-call `notifications/cancelled` propagate from the CLI to the in-flight responder so a cancel from the terminal correctly marks the question `cancelled: true`.
- Cost: every server restart invalidates active CLI sessions; agents drop the switchboard tools after the 404. Agents you want to keep working need `/exit` + relaunch after a restart. Mitigation: server startup auto-clears away mode globally, so pre-restart agents fall back to terminal output rather than getting stuck in a Stop-hook loop.
## Recovery when the turn-end hook blocks and MCP tools are unavailable

If the Switchboard MCP tools disconnect from your session while the away-mode flag is active, the turn-end hook will block every response with no way to call `set_away_mode(false)` or `notify_human`. Symptom: every turn ends with "Stop hook feedback: You are in away mode..." and `mcp__switchboard__*` tools are gone. Recovery, in order of preference:

1. **Toggle via the Android global pill chip (Page A top-bar)** — long-press -> Exit. Writes `global_settings/away_mode = false` to Firebase; the hook reads the server state and stops blocking.
2. **Restart the service** (`.\scripts\restart-service.ps1 -SkipTests`). Server startup writes `global_settings/away_mode = false`. Note: in stateful HTTP mode, restart also kills every active session's MCP tools — those agents need `/exit` + relaunch.
3. **Inspect `/healthz`** — reports per-listener state (`live` / `reconnecting` / `starting` / `stopped`), per-loop crash counts, and pending-question state. A `reconnecting` listener means the supervisor detected its SDK thread died and is rebuilding with exponential backoff — wait ~5 s and retry.

## Spawn flow

When the user taps "+" on the phone, they choose surface (Windows / WSL), project, optional prompt, and whether to create a new conversation or add the spawned agent into an existing one. The server dispatches via structured Firebase `/spawn_commands/` entries; `SpawnHandler.handle_fresh` and `handle_resume` are the two entry points. Spawn auto-enables global away mode if currently off.

**Spawn-root env vars** (both consumed by `config.py`):

- `SWITCHBOARD_WINDOWS_SPAWN_ROOT` — Windows root containing project folders (e.g. `C:\Work`). Legacy alias `SWITCHBOARD_SPAWN_ROOT` is still accepted.
- `SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT` — segment appended to the resolved WSL home to locate the WSL workspace root. Default `work`. The WSL surface is rejected at spawn time if the WSL home is unresolved.

`handle_fresh` and `handle_resume` call `_user_has_interactive_session()` (runs `quser`) before triggering the scheduled task and abort with a "Cannot spawn: no one is logged in..." message if no `Active` / `Disc` session exists. Without this, `schtasks /run` returns success even when there's no interactive desktop session for it to launch `wt` into. The gate degrades open if `quser` fails to launch.

## List-based UI

The Android client uses a list-based two-page nav:

- **Page A**: conversation list, ordered by last activity. Each row shows the title, relative timestamp, unseen-activity dot, AWAY badge (when global away mode is on), and the open-conversation accent border + "open" label. Swipe-right to end; swipe-left to hide. Long-press for a context menu: Resume, Combine into..., Hide/Unhide, End conversation. The Resume item is enabled when any of the conversation's member sessions has a terminal (ended/lost) registry record.
- **Page B**: per-conversation message view with the tab info popover and a reply input (visible only when there's a pending question).

Hidden conversations are accessible via the overflow menu's "Show hidden" toggle. FCM notification taps deep-link directly to Page B.

## Conventions

- **Python 3.11+, asyncio end-to-end.** No threads, no `run_in_executor` unless a blocking library forces it.
- **Tool handlers stay thin.** `ask_human` in `gateway/handlers.py` should create the pending record, broadcast it to surfaces, and `await future` — nothing else. Complexity lives in the per-surface modules.
- **Single mandatory backend.** Firebase is required; the server exits at startup with a ConfigError if its env vars are unset. Optional sub-features degrade gracefully (document delivery needs `FIREBASE_STORAGE_BUCKET`; spawn needs the spawn-root vars), but the core gateway does not start without Firebase.
- **Conversations persist; in-flight futures don't.** Conversation state persists in Firebase and rehydrates on restart (`server/hydration.py`); pending `ask_human` futures and wait queues are in-memory and do not survive restart. Don't add a second datastore without a design revision.
- **Comments sparingly.** Explain why, not what.
