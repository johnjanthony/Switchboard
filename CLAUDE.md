# Switchboard — Agent Orientation

Switchboard is a local MCP gateway that lets Claude Code agents pause mid-task and request human input from John. Responses come back from his phone. The gateway exists to support *away mode* — at-desk interaction continues to use the normal VS Code chat UI.

This file is the quick-orient doc for any Claude Code agent working **on Switchboard itself**. Agents that merely *consume* Switchboard don't need this — they just call `ask_human` / `notify_human`.

## Canonical design

- **Current design specs:**
  - [`docs/superpowers/specs/2026-04-22-unified-channel-routing-design.md`](docs/superpowers/specs/2026-04-22-unified-channel-routing-design.md) — unified channel routing (channel_id + sender), implemented
  - [`docs/superpowers/specs/2026-04-19-switchboard-design.md`](docs/superpowers/specs/2026-04-19-switchboard-design.md) — core gateway design
  - [`docs/superpowers/specs/2026-04-21-collab-sessions-design.md`](docs/superpowers/specs/2026-04-21-collab-sessions-design.md) — collab sessions (implemented; routing superseded by 2026-04-22)
- Earlier iterations retained for history only (do not implement from these):
  - [`docs/design-spec-v2.1.md`](docs/design-spec-v2.1.md) — redirect stub; content moved to the 2026-04-19 superpowers spec
  - [`docs/superpowers/specs/2026-04-18-switchboard-design.md`](docs/superpowers/specs/2026-04-18-switchboard-design.md) — superseded; proposed web UI + ntfy, later dropped
  - [`docs/claude-gateway-design-spec.md`](docs/claude-gateway-design-spec.md) — original two-option exploration

If any of these disagree, the 2026-04-22 spec wins for routing; the 2026-04-19 spec wins for everything else.

## Project shape

Single Python process, one asyncio event loop, MCP HTTP server on `localhost:9876`, with pluggable backends (Android/Firebase). No web UI, no ntfy.

Switchboard exists specifically for **away mode** — when John has stepped away and the VS Code chat UI is no longer being watched. At-desk interaction uses the normal VS Code chat channel.

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
  android.py           Android/Firebase MessengerBackend implementation
  firebase.py          Firebase admin logic for Android backend
  gateway.py           Tool handlers (ask_human, notify_human) + dispatch loops
  spawn.py             Claude Code session spawner (triggered from Android app)
  collab.py            CollabSession dataclass + session registry (see 2026-04-22 spec)
  logging_jsonl.py     JSONL audit log
scripts/
  install-service.ps1        One-time NSSM service install
  uninstall-service.ps1      Remove the service
  restart-service.ps1        Stop + pytest gate + start
  register-spawn-task.ps1    Re-register SwitchboardSpawn scheduled task
  spawn-launcher.ps1         Runs in user session to open a new wt tab
skill/
  SKILL.md             Installed into ~/.claude/skills/switchboard/
android/
  app/src/main/
    AndroidManifest.xml
    java/io/github/johnjanthony/switchboard/
      MainActivity.kt        Compose UI — tabs, chat view, message bubbles, spawn dialog
      MainViewModel.kt       Firebase listeners, question/notification/document state
      fcm/
        SwitchboardFirebaseMessagingService.kt  Push notifications (two channels, tap-to-tab)
      network/
        ApiService.kt        Question data class (Firebase deserialization)
      ui/theme/              Material3 dark theme
  app/build.gradle           Markwon, Firebase, Compose dependencies
logs/
  switchboard.jsonl    Runtime audit log (gitignored)
  sessions/            Per-channel conversation logs keyed by channel_id (gitignored)
```

(See the canonical spec §11 for the authoritative tree including tests.)

## Running locally

```bash
pip install -e ".[dev]"
# Either set FIREBASE_SERVICE_ACCOUNT_JSON and FIREBASE_DATABASE_URL as OS env vars,
# or create a .env file from .env.example and fill in the values.
python -m server
```

Gateway comes up on `http://127.0.0.1:9876`. Point a Claude Code agent at `http://localhost:9876/mcp` (HTTP transport, `"type": "http"` in `.claude.json`).

## Testing

```bash
pytest                 # all tests
pytest tests/test_registry.py -v
```

Integration tests run in-process; no external services required. The backends (Firebase, etc.) are mocked.

## Conventions

- **Python 3.11+, asyncio end-to-end.** No threads, no `run_in_executor` unless absolutely required for a blocking library.
- **Tool handlers stay thin.** `ask_human` in `gateway.py` should create the pending record, broadcast it to surfaces, and `await future` — nothing else. Complexity lives in the per-surface modules.
- **Surfaces are independently degradable.** Missing env vars disable a surface but the gateway still starts. Tests cover the degraded-startup paths.
- **No DB, no persistence.** Stick to the in-memory design unless the spec is formally revised.
- **Line endings: CRLF** (per John's global convention). Verify with `file <path>` after editing.
- **Comments sparingly.** Explain why, not what. See the top-level `CLAUDE.md` in John's home if you need the full protocol.

## Service management (Windows service via NSSM)

The server runs as a Windows service so it starts automatically and survives VS Code being closed.

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

**Agents rebuilding or restarting the service MUST use `-SkipTests`.** The pytest gate takes ~15 seconds, which consumes the auto-reconnect window (31 seconds) and causes the MCP connection to drop permanently. With `-SkipTests`, the service restarts in ~3 seconds and Claude Code auto-reconnects within the window.

Logs: `logs\switchboard.jsonl` (JSONL audit), `logs\nssm-stdout.log` / `nssm-stderr.log` (uvicorn console).

NSSM sets `AppDirectory=C:\Work\Switchboard` so `config.py`'s `.env` fallback resolves correctly — credentials stay in `.env`, not the registry.

## Away mode protocol

Away mode activates whenever John says he is stepping away — any phrasing like "I'm stepping away" or "stepping away" is sufficient. No explicit "use ask_human" instruction is required.

**When away mode activates, do not produce any text response in the terminal.** Make a tool call immediately:

- If idle or between tasks: `ask_human` to confirm you have entered away mode and ask what's next.
- If mid-task: `notify_human` to report current status, followed by `ask_human` to confirm next steps.

There is no valid reason to type a chat acknowledgment first. "Got it" in the terminal is a failure. The tool call is the acknowledgment. Every subsequent output — status updates, questions, task-done pings — continues through `ask_human` or `notify_human`.

**Receiving a reply to `ask_human` does not exit away mode.** Do not respond to a reply in the terminal. Your next output after receiving any reply must also be via `ask_human` or `notify_human` — even if the reply indicates the task is done or the session was a test.

The only exit from away mode is John explicitly saying he's back at his desk.

Use `notify_human` only for true fire-and-forget updates: progress reports, confirmations of non-blocking steps, "starting X now" pings. It must not be used as a substitute for `ask_human` when a response is needed, and must not be the final output of a session — there must always be at least one `ask_human` to follow. When in doubt, use `ask_human`.

**Restart behaviour differs by session type:**

- **Single-agent away mode:** restarting with `-SkipTests` is safe. The service restarts in ~3 seconds and Claude Code auto-reconnects within the 31-second window. Any pending `ask_human` is lost from the registry, but the agent times out and re-asks. Do not restart without `-SkipTests` — the pytest gate takes ~15 seconds and the connection drops permanently.
- **Collab sessions:** never restart while a collab session is active. The in-memory `CollabSession` state is permanently lost on restart — the connection may recover but the session cannot. Both agents will receive `"ERROR: session not found"` and the collaboration ends.

## What belongs in CLAUDE-JOURNAL.md

Any session that produces a decision, a spec revision, or a non-trivial implementation step logs an entry. Format: `## YYYY-MM-DD — short title`, then bullets for: what changed, why, files touched, open follow-ups. Keep entries terse — this is an audit trail, not prose.
