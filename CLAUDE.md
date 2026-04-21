# Switchboard — Agent Orientation

Switchboard is a local MCP gateway that lets Claude Code agents pause mid-task and request human input from the developer. Responses come back from Telegram on mobile. The gateway exists to support *away mode* — at-desk interaction continues to use the normal VS Code chat UI.

This file is the quick-orient doc for any Claude Code agent working **on Switchboard itself**. Agents that merely *consume* Switchboard don't need this — they just call `ask_human` / `notify_human`.

## Canonical design

- **Current design spec:** [`docs/superpowers/specs/2026-04-19-switchboard-design.md`](docs/superpowers/specs/2026-04-19-switchboard-design.md)
- Earlier iterations retained for history only (do not implement from these):
  - [`docs/design-spec-v2.1.md`](docs/design-spec-v2.1.md) — redirect stub; content moved to the 2026-04-19 superpowers spec
  - [`docs/superpowers/specs/2026-04-18-switchboard-design.md`](docs/superpowers/specs/2026-04-18-switchboard-design.md) — superseded; proposed web UI + ntfy, later dropped
  - [`docs/claude-gateway-design-spec.md`](docs/claude-gateway-design-spec.md) — original two-option exploration

If any of these disagree with the 2026-04-19 spec, the 2026-04-19 spec wins.

## Project shape

Single Python process, one asyncio event loop, MCP HTTP/SSE server on `localhost:9876`, Telegram bot as the only response surface. No web UI, no ntfy.

Switchboard exists specifically for **away mode** — when the developer has stepped away and the VS Code chat UI is no longer being watched. At-desk interaction uses the normal VS Code chat channel.

Registry is in-memory (`dict[request_id, asyncio.Future]`). Restart = pending requests are lost and waiting agents time out.

## Layout

```text
server/
  __init__.py          Package marker
  __main__.py          Enables `python -m server`
  main.py              Entry point — wires config, registry, backend, MCP, uvicorn
  config.py            Env-based Config loader (dotenv fallback)
  registry.py          PendingRequest + Registry (in-memory, correlation index)
  messenger.py         MessengerBackend ABC + IncomingResponse
  telegram.py          Telegram MessengerBackend implementation (httpx)
  gateway.py           Tool handlers (ask_human, notify_human) + dispatch loops
  spawn.py             Telegram-triggered Claude Code session spawner
  logging_jsonl.py     JSONL audit log
scripts/
  install-service.ps1        One-time NSSM service install
  uninstall-service.ps1      Remove the service
  restart-service.ps1        Stop + pytest gate + start
  register-spawn-task.ps1    Re-register SwitchboardSpawn scheduled task
  spawn-launcher.ps1         Runs in user session to open a new wt tab
skill/
  SKILL.md             Installed into ~/.claude/skills/switchboard/
logs/
  switchboard.jsonl    Runtime audit log (gitignored)
  sessions/            Per-agent ask_human conversation logs (gitignored)
```

(See the canonical spec §11 for the authoritative tree including tests.)

## Running locally

```bash
pip install -e ".[dev]"
# Either set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as OS env vars,
# or create a .env file from .env.example and fill in the values.
python -m server
```

Gateway comes up on `http://127.0.0.1:9876`. Point a Claude Code agent at `http://localhost:9876/sse`.

## Testing

```bash
pytest                 # all tests
pytest tests/test_registry.py -v
```

Integration tests run in-process; no external services required. The Telegram client is mocked.

## Conventions

- **Python 3.11+, asyncio end-to-end.** No threads, no `run_in_executor` unless absolutely required for a blocking library.
- **Tool handlers stay thin.** `ask_human` in `gateway.py` should create the pending record, broadcast it to surfaces, and `await future` — nothing else. Complexity lives in the per-surface modules.
- **Surfaces are independently degradable.** Missing env vars disable a surface but the gateway still starts. Tests cover the degraded-startup paths.
- **No DB, no persistence.** Stick to the in-memory design unless the spec is formally revised.
- **Line endings: CRLF** (per developer's global convention). Verify with `file <path>` after editing.
- **Comments sparingly.** Explain why, not what. See the top-level `CLAUDE.md` in the developer's home if you need the full protocol.

## Service management (Windows service via NSSM)

The server runs as a Windows service so it starts automatically and survives VS Code being closed.

```powershell
# One-time install (elevated PowerShell):
choco install nssm          # install NSSM via Chocolatey
.\scripts\install-service.ps1

# Check status:
nssm status switchboard

# Restart after code changes — stops service, runs pytest gate, restarts:
.\scripts\restart-service.ps1          # elevated PowerShell (do not run while in away mode)

# Re-register the SwitchboardSpawn scheduled task (if missing or after re-install):
.\scripts\register-spawn-task.ps1      # elevated PowerShell

# Remove the service:
.\scripts\uninstall-service.ps1  # elevated PowerShell
```

Logs: `logs\switchboard.jsonl` (JSONL audit), `logs\nssm-stdout.log` / `nssm-stderr.log` (uvicorn console).

NSSM sets `AppDirectory=C:\Work\Switchboard` so `config.py`'s `.env` fallback resolves correctly — credentials stay in `.env`, not the registry.

## Away mode protocol

Away mode activates whenever John says he is stepping away — any phrasing like "I'm stepping away" or "stepping away" is sufficient. No explicit "use ask_human" instruction is required.

**When away mode activates, do not produce any text response in the terminal.** Make a tool call immediately:

- If idle or between tasks: `ask_human` to confirm you have entered away mode and ask what's next.
- If mid-task: `notify_human` to report current status, followed by `ask_human` to confirm next steps.

There is no valid reason to type a chat acknowledgment first. "Got it" in the terminal is a failure. The tool call is the acknowledgment. Every subsequent output — status updates, questions, task-done pings — continues through `ask_human` or `notify_human`.

**Receiving a reply to `ask_human` does not exit away mode.** Do not respond to a Telegram reply in the terminal. Your next output after receiving any reply must also be via `ask_human` or `notify_human` — even if the reply indicates the task is done or the session was a test.

The only exit from away mode is John explicitly saying he's back at his desk.

Use `notify_human` only for true fire-and-forget updates: progress reports, confirmations of non-blocking steps, "starting X now" pings. It must not be used as a substitute for `ask_human` when a response is needed, and must not be the final output of a session — there must always be at least one `ask_human` to follow. When in doubt, use `ask_human`.

**Do not restart Switchboard while in away mode.** Restarting tears down the SSE connection — `ask_human` and `notify_human` stop working immediately. Return to desk before restarting.

## What belongs in CLAUDE-JOURNAL.md

Any session that produces a decision, a spec revision, or a non-trivial implementation step logs an entry. Format: `## YYYY-MM-DD — short title`, then bullets for: what changed, why, files touched, open follow-ups. Keep entries terse — this is an audit trail, not prose.
