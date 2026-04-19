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
  main.py              MCP server entry point
  gateway.py           Pending request management
  telegram.py          Telegram bot integration
  logging_jsonl.py     JSONL audit log
logs/
  switchboard.jsonl
```

(See v2.1 spec §Project Structure for the authoritative tree.)

## Running locally

```bash
pip install -e ".[dev]"
# Either set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as OS env vars,
# or create a .env file from .env.example and fill in the values.
python -m server.main
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

## What belongs in CLAUDE-JOURNAL.md

Any session that produces a decision, a spec revision, or a non-trivial implementation step logs an entry. Format: `## YYYY-MM-DD — short title`, then bullets for: what changed, why, files touched, open follow-ups. Keep entries terse — this is an audit trail, not prose.
