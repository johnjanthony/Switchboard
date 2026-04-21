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

## SHIPPED: Telegram UX — ForceReply

**Delivered 2026-04-20.** `send_question` in `server/telegram.py` now includes `"reply_markup": {"force_reply": True}`. Telegram auto-enters reply mode when a question arrives — eliminates the manual reply-gesture failure mode observed in the first smoke test.

Inline keyboard with suggestion buttons remains unshipped — pick up when a real yes/no/abort pattern shows up frequently in usage. See design notes below.

### Inline keyboard with suggestion buttons (unshipped)

Resurrect the `suggestions: list[str]` parameter on `ask_human` (was in the original design, cut for scope). Agent passes `["yes","no","abort"]`; Telegram renders tap-able buttons; tapping generates a `callback_query` update instead of a `message`. `TelegramBackend.poll_responses` currently only handles `message.reply_to_message` — it would need to also handle `callback_query` (including answering the callback with `answerCallbackQuery` so the Telegram UI stops showing a spinner). The callback's `data` field carries the chosen suggestion; correlation comes from `callback_query.message.message_id`. Substantive but contained.

---

## SHIPPED: Never-stop-asking in away mode

**Delivered 2026-04-20.** `skill/SKILL.md` updated with the "Staying alive in away mode" section. After completing a discrete developer-assigned task, the agent calls `ask_human("Task done: <summary>. What's next?", agent_id)` instead of ending its turn. `__TIMEOUT__` is treated as permission to end gracefully.

The "discrete task the developer handed to you" phrasing is load-bearing — prevents pinging between internal subtasks.

If agents mis-calibrate task boundaries in practice, mitigation is a gateway-side per-agent `ask_human` rate limit (one question per 30s). Not implemented yet.

---

## Richer message formatting

- Enable Telegram `parse_mode=HTML` on outbound messages. HTML mode has a small escape list (`<`, `>`, `&`) and supports `<b>`, `<i>`, `<code>`, `<pre>`, `<a href=>`. Gateway auto-escapes user-supplied text; an explicit `format: Literal["plain", "html"] = "plain"` parameter lets agents opt into formatting.
- **Deliberately skip `MarkdownV2`.** Its escape list (18 characters including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message.
- Agent-side usage: when calling `ask_human(question, agent_id, format="html")`, the agent is responsible for well-formed HTML. Skill updated to document the contract.

**Developer-confirmed:** `parse_mode=HTML` approved; MarkdownV2 explicitly rejected.

---

## File / document delivery

- A new tool `send_document_human(path: str, agent_id: str, caption: str | None = None)` using Telegram's `sendDocument` endpoint. Enables agents to deliver generated reports, diffs, logs, PRDs for on-phone review.
- **Security boundary is non-trivial.** Unsupervised agent + disk read + external upload = data-exfil vector. Constraints to enforce gateway-side:
  - `path` must resolve to within the project's current working directory (`os.getcwd()`). Reject absolute paths, `..`, and symlinks that escape.
  - Size cap at ~5MB (Telegram's limit is 50MB; we don't need that ceiling).
  - Denylist obvious secrets (`.env`, `service-account.json`, `*token*`, `*secret*`, `*.pem`, `*.key`).
  - Log the full resolved path + size + sha256 to the JSONL audit log on every call.
- Prior art: `c:\Work\AgentOrchestrator\src\main\java\com\google\agentorchestrator\notification\TelegramBotService.java` has the send-document pattern; `docs\TELEGRAM_FILE_ATTACHMENTS.md` documents it.

**Developer-confirmed security boundary:** cwd-only paths + 5MB cap + denylist + full audit log per call (path, size, sha256).

---

## Observability + reliability

- **`/healthz` extension.** Return JSON `{pending_count, oldest_pending_age_seconds, total_answered, preflight_ok}`. Check from phone before a deep-work session to confirm the gateway is sane.
- **Log rotation.** `logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).
- **Chat ID preflight.** Currently we only `getMe` at startup. Adding `getChat?chat_id=...` would catch misconfigured chat IDs at startup instead of on the first `ask_human` call.
- **Rate-limiting at the gateway.** An agent that calls `notify_human` 100 times in a minute would hammer Telegram and earn a 429. Simple token-bucket on outbound messages (e.g., 30/minute) would prevent self-inflicted rate-limiting.
- **Timeout snooze via Telegram reply.** If a 24h `ask_human` is approaching timeout, the developer could reply `snooze 2h` to extend the window. Implementation: dispatch loop intercepts replies matching a pattern, calls a new `registry.extend_timeout(request_id, seconds)` method that resets the wait clock.

---

## Telegram-triggered headless Claude Code spawn

Developer sends a command to the bot; Switchboard spawns a new `claude -p "<prompt>" --dangerously-skip-permissions` subprocess already in away-mode so the developer can kick off new work from the phone without returning to the laptop. Natural extension of the "keep working while I'm away" premise; lets the developer queue independent work streams.

**Security surface is real and distinct from anything v1 exposes.** v1 is a human-input gateway — an attacker with the bot token can answer questions posed to the developer, but cannot originate new work. Adding spawn changes that to arbitrary code execution via Telegram. Requires:

1. **Shared-secret prefix.** Command shape: `/spawn <SWITCHBOARD_SPAWN_TOKEN> <project-key> <prompt>`. `SWITCHBOARD_SPAWN_TOKEN` is a second env var distinct from the bot token, set at service-install time.
2. **Allowlist of project directories** via `SWITCHBOARD_SPAWN_PROJECTS="key1=/abs/path,key2=/abs/path"`. `<project-key>` resolves into it; unknown keys rejected.
3. **Mandatory audit-log entry** per spawn: timestamp, resolved working directory, full argv, PID post-spawn.
4. **Per-60-seconds rate limit** to slow down abuse.
5. **Acknowledgment reply** back to Telegram: `Spawning <project-key> with task '<prompt preview>'. PID: <n>.`

**Scope decision (confirmed 2026-04-20):** keep spawning inside Switchboard. Sibling-tool alternative rejected.

---

## SHIPPED: Run service as user account for spawn support

**Delivered 2026-04-20.** The NSSM service runs as SYSTEM, which lives in Windows Session 0 and has no access to the user's interactive desktop or app execution aliases. Any path-based workaround (injecting `AppData\Local\Microsoft\WindowsApps` into `AppEnvironmentExtra`, or hardcoding the versioned `Program Files\WindowsApps\wt.exe` path) is either fragile or breaks across Windows Terminal updates.

**Fix:** `install-service.ps1` now sets the service logon account to the installing user (`.\%USERNAME%`) via `nssm set switchboard ObjectName`. NSSM prompts for the account password at install time. Running as the user gives the service the correct PATH, app execution aliases, and desktop session — `wt.exe` resolves naturally.

The `SWITCHBOARD_WT_PATH` config field (added to `server/config.py`, `server/spawn.py`, `.env.example`) is retained as an escape hatch for non-standard setups, but is not needed for the normal install path.

**Tradeoff to know:** The service won't start if the account password changes (NSSM stores it at install time). Run `uninstall-service.ps1` + `install-service.ps1` to re-register with the new password.

---

## Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** More efficient at scale, but requires exposing a public HTTPS endpoint (or a tunnel). Not worth the infra for a single-user tool.
- **Multi-user chat support.** Single-developer model is baked into the spec (`TELEGRAM_CHAT_ID` is a scalar, not a list). Don't touch until there's a concrete second user.
- **MarkdownV2** (see rationale under "Richer message formatting").
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
