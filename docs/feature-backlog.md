# Switchboard Feature Backlog

Captured from a 2026-04-19 brainstorm with the developer. Items marked **SHIPPED** are complete; the rest are unscoped. When an item is picked up, it gets its own spec + plan per the existing workflow.

---

## SHIPPED: Always-on deployment

**Delivered 2026-04-20.** NSSM-wrapped Windows service (`switchboard`) installed via Chocolatey. Env vars sourced from `.env` via `config.py` dotenv fallback — no secrets in the registry. Three scripts in `scripts/`:

- `install-service.ps1` — installs service, applies correct `sc sdset` SDDL, starts it
- `uninstall-service.ps1` — stops and removes (requires admin)
- `restart-service.ps1` — stop + pytest gate + start (no admin required after install)

**SDDL lesson:** the `sc sdset` SDDL must include `WRITE_DAC` (`WD`) for admins (`BA`) and SYSTEM (`SY`), or even admins lose the ability to modify the descriptor and recovery requires deleting `HKLM:\SYSTEM\CurrentControlSet\Services\switchboard\Security\Security` and rebooting. The correct SDDL applied by `install-service.ps1` is:

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

## SHIPPED: Telegram UX — ForceReply

**Delivered 2026-04-20.** `send_question` in `server/telegram.py` now includes `"reply_markup": {"force_reply": True}`. Telegram auto-enters reply mode when a question arrives — eliminates the manual reply-gesture failure mode observed in the first smoke test.

Inline keyboard with suggestion buttons remains unshipped — pick up when a real yes/no/abort pattern shows up frequently in usage. See "Inline keyboard with suggestion buttons" below.

---

## SHIPPED: Never-stop-asking in away mode

**Delivered 2026-04-20.** `skill/SKILL.md` updated with the "Staying alive in away mode" section. After completing a discrete developer-assigned task, the agent calls `ask_human("Task done: <summary>. What's next?", agent_id)` instead of ending its turn. `__TIMEOUT__` is treated as permission to end gracefully.

The "discrete task the developer handed to you" phrasing is load-bearing — prevents pinging between internal subtasks.

If agents mis-calibrate task boundaries in practice, mitigation is a gateway-side per-agent `ask_human` rate limit (one question per 30s). Not implemented yet.

---

## SHIPPED: Telegram-triggered Claude Code spawn

**Delivered 2026-04-21.** Developer sends `/spawn [project-key] [prompt]` to the bot; Switchboard writes a `spawn-pending.json` file and triggers the `SwitchboardSpawn` Windows Scheduled Task, which runs `spawn-launcher.ps1` in the user's interactive desktop session (Session 1) — where `wt.exe` is available. The launcher opens a new Windows Terminal tab running `claude -p "<prompt>" --dangerously-skip-permissions`.

**Implementation vs original spec:** The shared-secret prefix (`SWITCHBOARD_SPAWN_TOKEN`) and per-project allowlist (`SWITCHBOARD_SPAWN_PROJECTS`) were simplified to a single `SWITCHBOARD_SPAWN_ROOT` directory. Sub-directory traversal is prevented by resolving paths against `spawn_root` and rejecting escapes. The bot token itself is the auth boundary. A 60-second rate limit is enforced per spawn.

**Session 0 isolation:** Windows services run in Session 0 with no access to the user desktop. The scheduled task (`SwitchboardSpawn`, `LogonType Interactive`, `RunLevel Limited`) crosses into Session 1 where `wt.exe` resolves naturally.

**Spawn-resume (abandoned 2026-04-21):** An attempt was made to add a `-Spawn` flag to `restart-service.ps1` that would restart the service AND spawn a `claude -c` session to resume the interrupted away-mode session. After investigation, no mechanism exists to make Claude Code proactively call `ask_human` at session start without a user turn — `-p`, positional args, `additionalContext` hooks, and transcript injection all either cause headless mode or fail to trigger inference. Decision: do not restart Switchboard while in away mode. See abandoned spec/plan in `docs/superpowers/specs/2026-04-20-spawn-resume-design.md`.

---

## SHIPPED: Richer message formatting

**Delivered 2026-04-21.** `ask_human` and `notify_human` accept an optional `format: "plain" | "html"` parameter (default `"plain"`). When `format="html"`, Telegram renders the message with `parse_mode=HTML` — supports `<b>`, `<i>`, `<code>`, `<pre>`, `<a href=>`. The gateway auto-escapes the agent_id/request_id prefix; the message body is the agent's responsibility.

**MarkdownV2 deliberately skipped.** Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message.

`skill/SKILL.md` updated with the `format` parameter contract, supported tags, and an explicit warning against Markdown syntax.

---

## SHIPPED: File / document delivery

**Delivered 2026-04-21.** New MCP tool `send_document_human(path, agent_id, caption?)` delivers files to the developer on Telegram via `sendDocument`. Fire-and-forget; per the "never end on fire-and-forget" rule, at least one `ask_human` must follow.

Security boundary enforced gateway-side:

- Relative paths only (no absolute, no `..` traversal, symlink escapes caught by `resolve()`)
- 5 MB cap
- Denylist (exact): `.env`, `service-account.json`
- Denylist (glob, case-insensitive): `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env`
- JSONL audit log per call: resolved path, size_bytes, sha256, caption_preview

**Known gap:** Telegram enforces a 1024-character caption limit. Oversized captions return `"ERROR: ..."` from the gateway rather than being validated upfront. Behavior is not silent; error message may be cryptic.

24 new tests added (total: 98). `skill/SKILL.md` updated with `send_document_human` docs and constraints.

---

## SHIPPED: Inline keyboard with suggestion buttons

**Delivered 2026-04-21.** `ask_human` now accepts `suggestions: list[str] | None = None`. When provided, Telegram renders tap-able inline buttons; the tapped label is returned as the response. Typed free-text replies still work via Telegram's manual reply gesture.

Implementation: `poll_responses` now handles both `message.reply_to_message` (typed replies) and `callback_query` (button taps). Button taps are acknowledged immediately via `answerCallbackQuery` to dismiss Telegram's spinner. Chat-ID filtering applied to callback_query updates (same as messages). `_answer_callback_query` failures are non-fatal — logged as surface_error, response still resolved.

When suggestions are provided, `send_question` sends `inline_keyboard` reply_markup instead of `force_reply`. The two are mutually exclusive in Telegram's API.

5 new tests (108 total). `skill/SKILL.md` updated with usage docs and 64-char label constraint.

---

## SHIPPED: Android app UI, Markdown rendering, and push notifications

**Delivered 2026-04-21.**

**Markdown rendering:** `format="html"` replaced by `format="markdown"` across the server tool API, skill doc, and Android client. The Android app renders messages via Markwon (with HtmlPlugin), replacing `HtmlCompat.fromHtml()`. Code spans (`\`backtick\``) render as cyan (`#4DD0E1`) monospace on a dark grey (`#2D2D2D`) background via Markwon's theme API. Code blocks preserve line breaks. Bold, italic, and links render natively.

**Dark theme:** Agent message bubbles use a black background; the chat area behind bubbles uses `surfaceVariant` (grey). "My" reply bubbles retain the Material3 `primaryContainer` colour.

**Notification format fix:** `notify_human` messages were not passing the `format` field through to the rendered `Message` object in `MainViewModel`. The `format` field is now read from the Firebase notification snapshot and passed correctly.

**Firebase deserialization fix:** `Question` data class fields changed from `val` to `var` to ensure Firebase Realtime Database can set all fields during deserialization. (The specific field affected was `format`; other fields appeared to work due to Firebase SDK version behaviour.)

**Push notifications:** `POST_NOTIFICATIONS` permission added to the manifest with a runtime request on first launch (Android 13+). Two notification channels: `switchboard_questions` (`IMPORTANCE_HIGH` — heads-up banner with sound) for `ask_human` calls, and `switchboard_updates` (`IMPORTANCE_DEFAULT`) for `notify_human` and documents. Notifications use unique IDs (AtomicInteger counter) so they stack rather than replace each other. Tapping a notification opens the app to the correct agent tab via `agent_id` extra on the `PendingIntent`.

**Known gap: Telegram + `format="markdown"`:** `telegram.py` checks `if format == "html":` to set `parse_mode=HTML`. If Telegram is re-enabled, agents using `format="markdown"` will get plain-text rendering on Telegram. Since Telegram is not in active use, this is deferred. Fix would require adding a `format == "markdown"` branch to `telegram.py` (using `parse_mode=MarkdownV2` with proper escaping, or mapping Markdown to HTML before sending).

---

## Android: suggestion buttons as notification actions

When `ask_human` is called with suggestions, render them as tappable action buttons on the notification banner so the developer can reply without opening the app.

**What it takes:**
- **Server (`firebase.py`)** — include suggestions as a JSON-encoded string in the FCM data payload alongside `request_id` and `agent_id`
- **New `NotificationReplyReceiver`** — a `BroadcastReceiver` that fires silently when an action button is tapped, writes the answer directly to Firebase `responses/{request_id}`, and dismisses the notification
- **FCM service** — parse suggestions from data payload, add up to 3 `addAction()` calls to the notification builder
- **Manifest** — register the receiver

**Constraint:** action buttons appear on the *expanded* notification, not the collapsed heads-up banner — the user swipes down on the banner to reveal them. Still faster than opening the app.

---

## Observability + reliability

- **`/healthz` extension.** Return JSON `{pending_count, oldest_pending_age_seconds, total_answered, preflight_ok}`. Check from phone before a deep-work session to confirm the gateway is sane.
- **Log rotation.** `logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).
- **Chat ID preflight.** Currently we only `getMe` at startup. Adding `getChat?chat_id=...` would catch misconfigured chat IDs at startup instead of on the first `ask_human` call.
- **Rate-limiting at the gateway.** An agent that calls `notify_human` 100 times in a minute would hammer Telegram and earn a 429. Simple token-bucket on outbound messages (e.g., 30/minute) would prevent self-inflicted rate-limiting.
- **Timeout snooze via Telegram reply.** If a 24h `ask_human` is approaching timeout, the developer could reply `snooze 2h` to extend the window. Implementation: dispatch loop intercepts replies matching a pattern, calls a new `registry.extend_timeout(request_id, seconds)` method that resets the wait clock.

---

## Maintenance & Housekeeping

- **Database ageout sweep.** Periodically clean up old questions, responses, and documents from Firebase (e.g., delete entries older than 30 days). This prevents the Realtime Database and Storage from growing indefinitely and keeps the Android app's history retrieval performant.

---

## Bring-your-own session: connect existing agents via a shared channel_id

Allow agents that were not spawned by Switchboard (e.g., already-running Claude Code sessions, manually launched terminals) to join a collab channel by passing a user-defined `channel_id` to `message_and_await_agent`.

Today, `channel_id` is generated by `SpawnHandler` and injected into the spawn prompt — agents have no way to opt into a session they weren't spawned into. This feature would let a developer define a `channel_id` themselves (e.g., `"myproject-debug-session"`), share it in two terminal prompts, and have both agents communicate through Switchboard without a `/spawn` command.

**What it takes:**
- **Implicit session registration** — when `message_and_await_agent` is called with an unknown `channel_id`, the server auto-creates a `CollabSession` and writes `sessions/{channel_id}/meta` to Firebase so the Android tab appears. No new MCP tool needed. The workflow is: agree on a `channel_id` out-of-band, both agents call `message_and_await_agent` as normal. The first caller creates the session and blocks; the second caller joins and the exchange begins.
- **No explicit `create_session` tool** — the typo-protection benefit of an explicit tool is marginal for a single-developer local gateway, and the complexity cost (new tool, SKILL.md additions, two-call workflow) outweighs it. A mistyped `channel_id` creates a phantom session that hangs and times out with `__TIMEOUT__` — self-correcting and no worse than the current error behavior.
- **SKILL.md** — document the bring-your-own workflow: agree on a `channel_id`, start both agents with matching `channel_id` and distinct `sender` values, call `message_and_await_agent` as normal.
- **Security consideration** — open registration means any agent knowing a `channel_id` string can join. Acceptable for a single-developer local gateway; would need a token or pre-registration step in a multi-user context.
- **Timeout/cleanup** — sessions with no activity for N hours should be garbage-collected from the registry (they already time out individually via `__TIMEOUT__`, but the `CollabSession` object lingers).

---

## Multi-CLI agent orchestration

When the `/spawn --agents=N` collaborative session feature ships (initially Claude-only), extend it to support heterogeneous agent CLIs — Gemini CLI, OpenCode, or any future CLI that can be invoked with a prompt and communicate via MCP.

**What it takes:**
- **`SWITCHBOARD_AGENT_BACKENDS` config** — ordered list of CLI backends to use per agent slot (e.g., `["claude", "gemini"]`). Falls back to all-Claude if not set.
- **CLI adapter abstraction in `spawn.py`** — each CLI backend needs its own command construction, session-resume flag, and output parsing (Claude uses `--resume session_id`; Gemini uses different flags).
- **Per-agent capability negotiation** — different CLIs may have different tool-call syntaxes or permission models. The system prompt injected at spawn time may need to be backend-specific.
- **Testing** — mock CLI adapters for integration tests so the test suite doesn't depend on external CLI installs.

**Constraint to watch:** Session resume (`--resume session_id`) is Claude-specific. Gemini and others may not have equivalent persistence. Multi-turn conversation state may need to be reconstructed by re-injecting history into the prompt.

---

## Android command-line build + deployment pipeline

Enable Claude Code agents to build the Android app and push it to John's phone without Android Studio.

**What it takes:**
- **Fix project structure** — the `android/` directory is missing project-level Gradle files (`gradlew`, `gradlew.bat`, `settings.gradle`, top-level `build.gradle`, `gradle/wrapper/`). Android Studio regenerates these; once present, command-line builds work via `./gradlew assembleDebug`.
- **ADB deployment** — `./gradlew installDebug` pushes the debug APK directly to a connected device over USB or ADB WiFi pairing. Works without any cloud service.
- **Optional: Firebase App Distribution** — for wireless-only deployment without ADB pairing, Firebase App Distribution can receive an APK upload and push an install prompt to John's phone. Requires adding the Firebase App Distribution Gradle plugin and a service account for CI auth.

**Recommended path:** fix project structure first (gradlew + settings.gradle), confirm `gradlew installDebug` works over ADB WiFi, then decide whether Firebase App Distribution is worth the added complexity.

---

## Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** More efficient at scale, but requires exposing a public HTTPS endpoint (or a tunnel). Not worth the infra for a single-user tool.
- **Multi-user chat support.** Single-developer model is baked into the spec (`TELEGRAM_CHAT_ID` is a scalar, not a list). Don't touch until there's a concrete second user.
- **MarkdownV2** (see rationale under "Richer message formatting").
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
