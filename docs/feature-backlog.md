# Switchboard Feature Backlog

Captured from a 2026-04-19 brainstorm with the developer. Items marked **SHIPPED** are complete; the rest are unscoped. When an item is picked up, it gets its own spec + plan per the existing workflow.

---

## SHIPPED: Always-on deployment

**Delivered 2026-04-20.** NSSM-wrapped Windows service (`switchboard`) installed via Chocolatey. Env vars sourced from `.env` via `config.py` dotenv fallback — no secrets in the registry. Three scripts in `scripts/`:

- `install-service.ps1` — installs service, applies correct `sc sdset` SDDL, starts it
- `uninstall-service.ps1` — stops and removes (requires admin)
- `restart-service.ps1` — stop + pytest gate + start (no admin required after install)

**SDDL lesson:** the `sc sdset` SDDL must include `WRITE_DAC` (`WD`) for admins (`BA`) and SYSTEM (`SY`), or even admins lose the ability to modify the descriptor and recovery requires deleting `HKLM:\SYSTEM\CurrentControlSet\Services\switchboard\Security\Security` registry value; SCM regenerated default SDDL at boot. The correct SDDL applied by `install-service.ps1` is:

```text
D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCLCSWLOCRRC;;;AU)(A;;CCLCSWRPWPCR;;;IU)
```

Task Scheduler stepping-stone was skipped per developer preference.

---

## SHIPPED: Run service as user account for spawn support

**Delivered 2026-04-20.** The NSSM service runs as SYSTEM, which lives in Windows Session 0 and has no access to the user's interactive desktop or app execution aliases. Any path-based workaround (injecting `AppData\Local\Microsoft\WindowsApps` into `AppEnvironmentExtra`, or hardcoding the versioned `Program Files\WindowsApps\wt.exe` path) is either fragile or breaks across Windows Terminal updates.

**Fix:** `install-service.ps1` now sets the service logon account to the installing user (`.\%USERNAME%`) via `nssm set switchboard ObjectName`. NSSM prompts for the account password at install time. Running as the user gives the service the correct PATH, app execution aliases, and desktop session — `wt.exe` resolves naturally.

**Tradeoff to know:** The service won't start if the account password changes (NSSM stores it at install time). Run `uninstall-service.ps1` + `install-service.ps1` to re-register with the new password.

---

## SHIPPED (SUPERSEDED): Telegram UX — ForceReply

**Delivered 2026-04-20. Superseded 2026-04-21.** `send_question` in `server/telegram.py` included `"reply_markup": {"force_reply": True}`. Telegram auto-entered reply mode when a question arrived — eliminated the manual reply-gesture failure mode observed in the first smoke test.

**Telegram has since been decommissioned** (`server/telegram.py` removed). The Android app + Firebase backend replaced it entirely. ForceReply is no longer applicable; suggestion buttons were shipped natively for Android (see "Inline keyboard with suggestion buttons" below).

---

## SHIPPED: Never-stop-asking in away mode

**Delivered 2026-04-20.** `skill/SKILL.md` updated with the "Staying alive in away mode" section. After completing a discrete developer-assigned task, the agent calls `ask_human("Task done: <summary>. What's next?", channel_id)` instead of ending its turn. `__TIMEOUT__` is treated as permission to end gracefully.

The "discrete task the developer handed to you" phrasing is load-bearing — prevents pinging between internal subtasks.

If agents mis-calibrate task boundaries in practice, mitigation is a gateway-side per-agent `ask_human` rate limit (one question per 30s). Not implemented yet.

---

## SHIPPED: Agent CLI spawn via scheduled task

**Delivered 2026-04-21 (Telegram trigger); trigger replaced by Android spawn dialog 2026-04-22.** Switchboard writes a `spawn-pending.json` file and triggers the `SwitchboardSpawn` Windows Scheduled Task, which runs `spawn-launcher.ps1` in the user's interactive desktop session (Session 1) — where `wt.exe` is available. The launcher opens a new Windows Terminal tab running `claude -p "<prompt>" --dangerously-skip-permissions`.

The original trigger was a Telegram `/spawn [project-key] [prompt]` bot command. After Telegram was decommissioned, the spawn dialog moved into the Android app (a floating action button that opens a form). The scheduled task and launcher script are unchanged.

**Implementation vs original spec:** The shared-secret prefix (`SWITCHBOARD_SPAWN_TOKEN`) and per-project allowlist (`SWITCHBOARD_SPAWN_PROJECTS`) were simplified to a single `SWITCHBOARD_SPAWN_ROOT` directory. Sub-directory traversal is prevented by resolving paths against `spawn_root` and rejecting escapes. The bot token itself is the auth boundary. A 60-second rate limit is enforced per spawn.

**Session 0 isolation:** Windows services run in Session 0 with no access to the user desktop. The scheduled task (`SwitchboardSpawn`, `LogonType Interactive`, `RunLevel Limited`) crosses into Session 1 where `wt.exe` resolves naturally.

**Spawn-resume (abandoned 2026-04-21):** An attempt was made to add a `-Spawn` flag to `restart-service.ps1` that would restart the service AND spawn a `claude -c` session to resume the interrupted away-mode session. After investigation, no mechanism exists to make Claude Code proactively call `ask_human` at session start without a user turn — `-p`, positional args, `additionalContext` hooks, and transcript injection all either cause headless mode or fail to trigger inference. Decision: do not restart Switchboard while in away mode. See abandoned spec/plan in `docs/superpowers/specs/2026-04-20-spawn-resume-design.md`.

---

## SHIPPED: Richer message formatting

**Delivered 2026-04-21.** `ask_human` and `notify_human` accept an optional `format: "plain" | "html"` parameter (default `"plain"`). When `format="html"`, Telegram rendered the message with `parse_mode=HTML` — supports `<b>`, `<i>`, `<code>`, `<pre>`, `<a href=>`. The gateway auto-escapes the agent_id/request_id prefix; the message body is the agent's responsibility.

**MarkdownV2 deliberately skipped.** Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message.

**Updated 2026-04-21 (Android delivery):** `format="html"` was renamed to `format="markdown"` across the server tool API, skill doc, and Android client. The Android app renders Markdown via Markwon; `format="html"` is no longer a valid value. See "Android app UI, Markdown rendering, and push notifications" for current behavior.

---

## SHIPPED: File / document delivery

**Delivered 2026-04-21.** New MCP tool `send_document_human(path, channel_id, sender?, caption?)` delivers files to the developer via Firebase Storage + Android app. Fire-and-forget; per the "never end on fire-and-forget" rule, at least one `ask_human` must follow.

Security boundary enforced gateway-side:

- Relative paths only (no absolute, no `..` traversal, symlink escapes caught by `resolve()`)
- 5 MB cap
- Denylist (exact): `.env`, `service-account.json`
- Denylist (glob, case-insensitive): `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env`
- JSONL audit log per call: resolved path, size_bytes, sha256, caption_preview

24 new tests added (total: 98). `skill/SKILL.md` updated with `send_document_human` docs and constraints.

---

## SHIPPED: Inline keyboard with suggestion buttons

**Delivered 2026-04-21.** `ask_human` now accepts `suggestions: list[str] | None = None`. When provided, the Android app renders tap-able buttons inline in the message bubble; the tapped label is returned as the response. Typed free-text replies still work via the compose box.

Implementation: suggestions are stored in the Firebase `sessions/{channel_id}/messages/{msg_id}` document alongside the question. The Android `ChannelView` composable renders them as `Button` rows below the message text. Tapping writes the chosen label to `responses/{request_id}` in Firebase, which the server's response listener picks up and resolves.

**Original Telegram implementation (superseded):** used `inline_keyboard` reply_markup; taps were handled via `callback_query` updates and acknowledged with `answerCallbackQuery`. Telegram is no longer active.

`skill/SKILL.md` updated with usage docs and 64-char label constraint.

---

## SHIPPED: Android app UI, Markdown rendering, and push notifications

**Delivered 2026-04-21.**

**Markdown rendering:** `format="html"` replaced by `format="markdown"` across the server tool API, skill doc, and Android client. The Android app renders messages via Markwon (with HtmlPlugin), replacing `HtmlCompat.fromHtml()`. Code spans (`\`backtick\``) render as cyan (`#4DD0E1`) monospace on a dark grey (`#2D2D2D`) background via Markwon's theme API. Code blocks preserve line breaks. Bold, italic, and links render natively.

**Dark theme:** Agent message bubbles use a black background; the chat area behind bubbles uses `surfaceVariant` (grey). "My" reply bubbles retain the Material3 `primaryContainer` colour.

**Notification format fix:** `notify_human` messages were not passing the `format` field through to the rendered `Message` object in `MainViewModel`. The `format` field is now read from the Firebase notification snapshot and passed correctly.

**Firebase deserialization fix:** `Question` data class fields changed from `val` to `var` to ensure Firebase Realtime Database can set all fields during deserialization. (The specific field affected was `format`; other fields appeared to work due to Firebase SDK version behaviour.)

**Push notifications:** `POST_NOTIFICATIONS` permission added to the manifest with a runtime request on first launch (Android 13+). Two notification channels: `switchboard_questions` (`IMPORTANCE_HIGH` — heads-up banner with sound) for `ask_human` calls, and `switchboard_updates` (`IMPORTANCE_DEFAULT`) for `notify_human` and documents. Notifications use unique IDs (AtomicInteger counter) so they stack rather than replace each other. Tapping a notification opens the app to the correct channel tab via `channel_id` extra on the `PendingIntent`.

---

## SHIPPED: Unified channel routing

**Delivered 2026-04-22.** Replaced `agent_id` with two orthogonal concepts: `channel_id` (routing key, spawn-time stable, format `{project_key}-{YYYYMMDD}-{HHmmSS}`) and `sender` (display label, e.g. `"Claude"`, `"Agent 1"`). All four MCP tools now use `channel_id` + `sender`.

All messages write to `sessions/{channel_id}/messages/{msg_id}` in Firebase — a single unified path for all message types (`question`, `notify`, `agent`, `document`) — eliminating the 3-tab collab problem. Session meta is written at spawn time so the Android tab appears immediately.

`--agents=N --relay` spawn flags replaced by `--collab`. Android spawn dialog: agents stepper + relay checkbox replaced by a single **Collab mode** checkbox. Collab sessions are limited to exactly 2 agents.

Android data model unified: `ChannelMessage` + `Channel` replace the four legacy types. Single `setupChannelsListener()` and `ChannelView` composable handle both single-agent and collab channels.

`logging_jsonl.py` fields renamed from `agent_id` to `channel_id` throughout.

---

## SHIPPED: Multi-CLI agent orchestration (Gemini support)

**Delivered 2026-04-23.** Extended the spawning system to support Gemini CLI alongside Claude Code. Android UI updated with independent **Claude** and **Gemini** checkboxes. `/spawn` command now accepts `--claude` and `--gemini` flags; heterogeneous collab sessions (mixed backends) are supported. `spawn-launcher.ps1` updated to execute the appropriate CLI backend. Legacy `--collab` flag remains for backward compatibility.

---

## SHIPPED: Android command-line build + deployment pipeline

**Delivered 2026-04-23.** Added missing project-level Gradle files (`gradlew`, `settings.gradle`, etc.) to the `android/` directory and verified command-line builds. Created `scripts/install-client.ps1` to automate the build and deployment process to a connected device via ADB.

---

## Web Dashboard for Conversation Monitoring & Interaction

**Proposed 2026-04-23.** A desktop-based web interface to supplement the Android app, allowing for more comfortable long-form replies and better visibility into multiple simultaneous sessions.

**Key Features:**
- **Real-time Monitoring**: Stream all active sessions from Firebase Realtime Database with a multi-pane or tabbed view.
- **Full Interaction**: Mirror the Android app's interactive capabilities:
    - Reply to `ask_human` prompts (including suggestion button support).
    - Inject messages into collaborative channels (`message_and_await_agent`).
- **Session Management**: View historical (closed) sessions and audit logs.
- **Visual Cues**: High-visibility indicators for pending questions and unseen activity, synced with the Android app's state.

**Technical Approach:**
- **Frontend**: A lightweight Single Page App (React, Vue, or vanilla JS) using the Firebase Web SDK for direct RTDB binding.
- **Deployment**: Can be hosted via Firebase Hosting for remote access or served locally by the Switchboard server (e.g., via FastAPI static files) for a "local-first" experience.

---

## Resilience and Protocol Enforcement

- **Silence Detection at the Gateway.** *(Spec'd 2026-04-23 as "Away-Mode Enforcement" — Stop-hook + server-side flag approach replaces gateway transcript inspection. See [`docs/superpowers/specs/2026-04-23-away-mode-enforcement-design.md`](docs/superpowers/specs/2026-04-23-away-mode-enforcement-design.md).)*
- **Away-Mode Framing Check.** Add an automated check to ensure that every agent response in away mode starts with a tool call.
- **Skill Instruction Polish.** Periodically review and harden `SKILL.md` based on failure patterns (e.g., the 2026-04-23 terminal leak incident).

---

## Per-channel away-mode tracking

Upgrade V1's global `away_mode_active` flag to per-`channel_id` state. The Stop hook would correlate its Claude Code `session_id` or `cwd` to a `channel_id` so only the session that is actually away gets blocked. Needed if John routinely runs one at-desk Claude Code session alongside an away-mode session — the global V1 flag would block Stop events in both.

**What it takes:**

- Server tracks `dict[channel_id, AwayState]` instead of a single bool.
- `enter_away_mode(channel_id)` / `exit_away_mode(channel_id)` accept a `channel_id` argument.
- Correlation mechanism: either (a) the tools also record `cwd` (passed by the agent or inferred) so the hook can query `GET /away-mode?cwd=<hook_cwd>`, or (b) a handshake where the agent writes a marker file keyed by cwd that the hook reads directly without HTTP.
- Sidecar persistence updated to a list of entries.

**Bundled enhancement: hook captures the leaked terminal text.** Once the hook knows the channel, it can read the last assistant message from the turn-end event stdin payload and forward it via `notify_human` on that channel before emitting the block/deny JSON. For Gemini, the field is documented as `prompt_response` on the `AfterAgent` payload — free to use. For Claude, the Stop payload field is not documented (verify at implementation time) — fall back to parsing `transcript_path` if absent. Result: the text the agent tried to leak to the terminal actually reaches John's phone instead of being lost, and the agent is still redirected to route the next turn through `ask_human`. Fair-game to do as part of this upgrade because without per-channel state the hook does not know where to post.

See [`docs/superpowers/specs/2026-04-23-away-mode-enforcement-design.md`](docs/superpowers/specs/2026-04-23-away-mode-enforcement-design.md) for the V1 global-flag design this would replace.

---

## Android: MRU workspace selector in spawn dialog

The spawn dialog currently requires typing the workspace path each time. Add an MRU-style dropdown that remembers workspaces John has spawned into before, so repeat spawns are a tap rather than a retype. New paths entered via free text are added to the MRU list; the dropdown shows them in most-recently-used order.

**What it takes:**

- Android persists the MRU list locally (SharedPreferences or DataStore) — no server-side change needed.
- Spawn dialog layout changes: text field becomes a combo-box-style control (editable dropdown) that surfaces the MRU list while still accepting free-text entry for first-time paths.
- Cap list size (e.g. 10 entries) and evict least-recently-used when full.
- Successful spawn promotes the chosen entry to the top; failed spawn (server rejects the path) does not add it to the list.

---

## Android: suggestion buttons as notification actions

When `ask_human` is called with suggestions, render them as tappable action buttons on the notification banner so the developer can reply without opening the app.

**What it takes:**

- **Server (`firebase.py`)** — include suggestions as a JSON-encoded string in the FCM data payload alongside `request_id` and `channel_id`
- **New `NotificationReplyReceiver`** — a `BroadcastReceiver` that fires silently when an action button is tapped, writes the answer directly to Firebase `responses/{request_id}`, and dismisses the notification
- **FCM service** — parse suggestions from data payload, add up to 3 `addAction()` calls to the notification builder
- **Manifest** — register the receiver

**Constraint:** action buttons appear on the *expanded* notification, not the collapsed heads-up banner — the user swipes down on the banner to reveal them. Still faster than opening the app.

---

## SHIPPED: `/healthz` endpoint

Returns JSON `{pending_count, oldest_pending_age_seconds, total_answered, preflight_ok}`. Check from phone before a deep-work session to confirm the gateway is sane. Note: `preflight_ok` was a Telegram preflight check and is now legacy (always `true`).

---

## Observability + reliability

- **Log rotation.** `logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).
- **Rate-limiting at the gateway.** *(SHIPPED 2026-04-23 — per-channel token bucket on `notify_human` and `send_document_human`; `ask_human` not yet rate-limited.)* An agent that calls `notify_human` 100 times in a minute would hammer the backend and potentially trigger FCM rate limits. Simple token-bucket on outbound messages (e.g., 30/minute) would prevent this.
- **Timeout snooze via Android app.** Add a "Snooze" button to the `ask_human` notification/tab that extends the window by 2h. Implementation: Android app writes `snooze: true` to the question object; gateway intercepts the change and resets the wait clock in the registry.

---

## Maintenance & Housekeeping

- **Database ageout sweep.** Periodically clean up old questions, responses, and documents from Firebase (e.g., delete entries older than 30 days). This prevents the Realtime Database and Storage from growing indefinitely and keeps the Android app's history retrieval performant.

---

## SHIPPED: Bring-your-own session

**Delivered 2026-04-23.** Agents not spawned by Switchboard can join a collab channel by calling `message_and_await_agent` with a shared `channel_id` provided by John. The first caller implicitly creates a `CollabSession`; senders enroll dynamically (max 2, duplicate names rejected). A pre-enrollment buffer makes call ordering irrelevant — either agent may call first, with or without a message.

Three background tasks fire on BYO session creation: `write_session_meta` (Android tab appears immediately), a sidecar write to `collab-sessions.json` (ensures "session lost" notification on gateway restart), and `start_inject_listener` (compose box wired up for human injection).

`SKILL.md` collab section rewritten to clearly separate away mode (John absent, all output via `xxx_human` tools) from collab mode (`message_and_await_agent`, does not imply away mode). BYO sessions default `sender` to the agent's own display name; John provides an override only when two instances of the same agent type need to be distinguished.

See [`docs/superpowers/specs/2026-04-23-bring-your-own-session-design.md`](docs/superpowers/specs/2026-04-23-bring-your-own-session-design.md).

---

## Symmetric spawned collab sessions

Remove `_LISTENER_NOTE` from Agent 2's spawn prompt in `spawn.py` so both spawned collab agents receive the same task prompt and work in parallel before exchanging findings — matching the BYO session model. Currently Agent 2 is explicitly told to call `message_and_await_agent` with no message, which prevents parallel work. The `_pending` queue in `CollabSession` already handles the both-with-messages case correctly, so this is purely a prompt change. The collab protocol hint in SKILL.md ("the first response you receive may be your partner's independent opening position") already documents the expected behaviour.

---

## Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** legacy Telegram concept, no longer applicable.
- **Multi-user chat support.** Single-developer model is baked into the spec. Don't touch until there's a concrete second user.
- **MarkdownV2** (see rationale under "Richer message formatting").
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
