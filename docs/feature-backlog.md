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

## Inline keyboard with suggestion buttons

Resurrect the `suggestions: list[str]` parameter on `ask_human` (was in the original design, cut for scope). Agent passes `["yes","no","abort"]`; Telegram renders tap-able buttons; tapping generates a `callback_query` update instead of a `message`. `TelegramBackend.poll_responses` currently only handles `message.reply_to_message` — it would need to also handle `callback_query` (including answering the callback with `answerCallbackQuery` so the Telegram UI stops showing a spinner). The callback's `data` field carries the chosen suggestion; correlation comes from `callback_query.message.message_id`. Substantive but contained.

Deferred until a real yes/no/abort pattern shows up frequently in usage.

---

## Observability + reliability

- **`/healthz` extension.** Return JSON `{pending_count, oldest_pending_age_seconds, total_answered, preflight_ok}`. Check from phone before a deep-work session to confirm the gateway is sane.
- **Log rotation.** `logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).
- **Chat ID preflight.** Currently we only `getMe` at startup. Adding `getChat?chat_id=...` would catch misconfigured chat IDs at startup instead of on the first `ask_human` call.
- **Rate-limiting at the gateway.** An agent that calls `notify_human` 100 times in a minute would hammer Telegram and earn a 429. Simple token-bucket on outbound messages (e.g., 30/minute) would prevent self-inflicted rate-limiting.
- **Timeout snooze via Telegram reply.** If a 24h `ask_human` is approaching timeout, the developer could reply `snooze 2h` to extend the window. Implementation: dispatch loop intercepts replies matching a pattern, calls a new `registry.extend_timeout(request_id, seconds)` method that resets the wait clock.

---

## Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** More efficient at scale, but requires exposing a public HTTPS endpoint (or a tunnel). Not worth the infra for a single-user tool.
- **Multi-user chat support.** Single-developer model is baked into the spec (`TELEGRAM_CHAT_ID` is a scalar, not a list). Don't touch until there's a concrete second user.
- **MarkdownV2** (see rationale under "Richer message formatting").
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
