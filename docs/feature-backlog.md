# Switchboard Feature Backlog

Captured from a 2026-04-19 brainstorm with the developer. Each item has a recommendation and alternatives; none are scoped as work yet. When one is picked up, it gets its own spec + plan per the existing workflow.

## Always-on deployment

- **Task Scheduler "At logon" task** running `python -m server` — simplest path to "always running when I'm logged in." Zero extra dependencies. Recommended as the first step before investing in a proper service.
- **Windows service wrapping** — NSSM or winsw to run the process as a LocalSystem / dedicated-account service that survives logout. Pick this only if Task Scheduler's logon-scoped lifecycle proves unreliable. Environment variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) must be registered at the service level via `nssm set switchboard AppEnvironmentExtra`. Debugging is harder (no stdout terminal), but the JSONL audit log at `logs/switchboard.jsonl` already covers runtime observability.
- **Companion restart script** (PowerShell): stop → `pytest -q` gate → start, with `nssm status switchboard` as the final check. The "rebuild" step is nominal since we install in editable (`pip install -e`) mode — code changes are picked up on next Python process start.

## Telegram UX: reply friction

- **`ForceReply` on every outbound question** (one-line change: add `"reply_markup": {"force_reply": true}` to the payload in `TelegramBackend._post_send_message` for the `send_question` call-site). Auto-puts Telegram into reply mode for the user — solves the "I forgot to use the reply gesture" failure mode observed in the first smoke test. **Highest ROI of any item on this list.**
- **Inline keyboard with suggestion buttons** — resurrect the `suggestions: list[str]` parameter on `ask_human` (was in the original design, cut for scope). Agent passes `["yes","no","abort"]`; Telegram renders tap-able buttons; tapping generates a `callback_query` update instead of a `message`. `TelegramBackend.poll_responses` currently only handles `message.reply_to_message` — it would need to also handle `callback_query` (including answering the callback with `answerCallbackQuery` so the Telegram UI stops showing a spinner). The callback's `data` field carries the chosen suggestion; correlation comes from `callback_query.message.message_id`. Substantive but contained.
- Do `ForceReply` first. Add suggestion buttons later when a real yes/no/abort pattern shows up frequently in usage.

## Richer message formatting

- Enable Telegram `parse_mode=HTML` on outbound messages. HTML mode has a small escape list (`<`, `>`, `&`) and supports `<b>`, `<i>`, `<code>`, `<pre>`, `<a href=>`. Gateway auto-escapes user-supplied text; an explicit `format: Literal["plain", "html"] = "plain"` parameter lets agents opt into formatting.
- **Deliberately skip `MarkdownV2`.** Its escape list (18 characters including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message.
- Agent-side usage: when calling `ask_human(question, agent_id, format="html")`, the agent is responsible for well-formed HTML. Skill updated to document the contract.

## File / document delivery

- A new tool `send_document_human(path: str, agent_id: str, caption: str | None = None)` using Telegram's `sendDocument` endpoint. Enables agents to deliver generated reports, diffs, logs, PRDs for on-phone review.
- **Security boundary is non-trivial.** Unsupervised agent + disk read + external upload = data-exfil vector. Constraints to enforce gateway-side:
  - `path` must resolve to within the project's current working directory (`os.getcwd()`). Reject absolute paths, `..`, and symlinks that escape.
  - Size cap at ~5MB (Telegram's limit is 50MB; we don't need that ceiling).
  - Denylist obvious secrets (`.env`, `service-account.json`, `*token*`, `*secret*`, `*.pem`, `*.key`).
  - Log the full resolved path + size + sha256 to the JSONL audit log on every call.
- Prior art: `c:\Work\AgentOrchestrator\src\main\java\com\google\agentorchestrator\notification\TelegramBotService.java` has the send-document pattern; `docs\TELEGRAM_FILE_ATTACHMENTS.md` documents it.

## Observability + reliability (complementary ideas)

- **`/healthz` extension.** Return JSON `{pending_count, oldest_pending_age_seconds, total_answered, preflight_ok}`. Check from phone before a deep-work session to confirm the gateway is sane.
- **Log rotation.** `logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).
- **Chat ID preflight.** Currently we only `getMe` at startup. Adding `getChat?chat_id=...` would catch misconfigured chat IDs at startup instead of on the first `ask_human` call.
- **Rate-limiting at the gateway.** An agent that calls `notify_human` 100 times in a minute would hammer Telegram and earn a 429. Simple token-bucket on outbound messages (e.g., 30/minute) would prevent self-inflicted rate-limiting.
- **Timeout snooze via Telegram reply.** If a 24h `ask_human` is approaching timeout, the developer could reply `snooze 2h` to extend the window. Implementation: dispatch loop intercepts replies matching a pattern, calls a new `registry.extend_timeout(request_id, seconds)` method that resets the wait clock.

## Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** More efficient at scale, but requires exposing a public HTTPS endpoint (or a tunnel). Not worth the infra for a single-user tool.
- **Multi-user chat support.** Single-developer model is baked into the spec (`TELEGRAM_CHAT_ID` is a scalar, not a list). Don't touch until there's a concrete second user.
- **MarkdownV2** (see rationale under "Richer message formatting").

## Developer-confirmed choices from this brainstorm

- **Always-on deployment:** skip the Task-Scheduler stepping-stone; go straight to **NSSM-wrapped Windows service**.
- **Richer formatting:** `parse_mode=HTML` (confirmed; MarkdownV2 explicitly skipped).
- **Document delivery security boundary:** cwd-only paths + 5MB cap + denylist (`.env`, `service-account.json`, `*token*`, `*secret*`, `*.pem`, `*.key`) + full audit log per call (path, size, sha256). Confirmed.

## Telegram-triggered headless Claude Code spawn

Developer sends a command to the bot; Switchboard spawns a new `claude -p "<prompt>" --dangerously-skip-permissions` subprocess already in away-mode so the developer can kick off new work from the phone without returning to the laptop. Natural extension of the "keep working while I'm away" premise; lets the developer queue independent work streams.

**Security surface is real and distinct from anything v1 exposes.** v1 is a human-input gateway — an attacker with the bot token can answer questions posed to the developer, but cannot originate new work. Adding spawn changes that to arbitrary code execution via Telegram. Requires:

1. **Shared-secret prefix.** Command shape: `/spawn <SWITCHBOARD_SPAWN_TOKEN> <project-key> <prompt>`. `SWITCHBOARD_SPAWN_TOKEN` is a second env var distinct from the bot token, set at service-install time.
2. **Allowlist of project directories** via `SWITCHBOARD_SPAWN_PROJECTS="key1=/abs/path,key2=/abs/path"`. `<project-key>` resolves into it; unknown keys rejected.
3. **Mandatory audit-log entry** per spawn: timestamp, resolved working directory, full argv, PID post-spawn.
4. **Per-60-seconds rate limit** to slow down abuse.
5. **Acknowledgment reply** back to Telegram: `Spawning <project-key> with task '<prompt preview>'. PID: <n>.`

Recommended scope: keep in Switchboard (rejected the alternative of a sibling `remote-claude` tool — too much duplicated infra for a single-developer setup).

**Scope decision (confirmed 2026-04-20):** keep spawning inside Switchboard. Sibling-tool alternative rejected.

## Never-stop-asking in away-mode

Keep the session alive across task completions so the developer can queue additional work from the phone without re-spawning. Cheapest implementation is a `SKILL.md` update; no gateway or backend changes required.

**Recommended skill-level addition:**

> While in away-mode, after completing a discrete task **that the developer handed to you** (not merely an intermediate step within that task), call `ask_human("Task done: <one-line summary>. What's next?", agent_id)` instead of ending your turn. Treat `__TIMEOUT__` as permission to end the session gracefully.

The "discrete task that the developer handed to you" phrasing is load-bearing — prevents the agent from pinging between intermediate subtasks like running tests or reading files.

**Risk:** agents may mis-calibrate where task boundaries sit. Mitigation if it surfaces in practice: add a gateway-side per-agent `ask_human` rate limit (e.g., one question per 30s) as a safety net — bouncy agents get throttled without changing the skill text.

Interaction with the spawn feature: if sessions stay alive indefinitely, new spawns are rarer. Both features are still useful — spawn handles "start parallel work stream," never-stop handles "keep this work stream receptive."

**Scope decision (confirmed 2026-04-20):** implement as a skill-wide `SKILL.md` update with the "discrete task the developer handed you" phrasing as the trigger. Per-handoff prompt alternative rejected.
