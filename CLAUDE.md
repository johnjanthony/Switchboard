# Switchboard — Agent Orientation

Switchboard is a local MCP gateway that lets Claude Code agents pause mid-task and request human input from the developer. Responses come back from one of three surfaces: a pinned-tab web UI at desk, ntfy toast action buttons at desk, or Telegram on mobile.

This file is the quick-orient doc for any Claude Code agent working **on Switchboard itself**. Agents that merely *consume* Switchboard don't need this — they just call `ask_human` / `notify_human`.

## Canonical design

- Current design spec: [`docs/superpowers/specs/2026-04-18-switchboard-design.md`](docs/superpowers/specs/2026-04-18-switchboard-design.md)
- Original two-option exploration (kept for history): [`docs/claude-gateway-design-spec.md`](docs/claude-gateway-design-spec.md)

If the two disagree, the superpowers spec wins — the original was pre-decision.

## Project shape

Single Python process, one asyncio event loop, Starlette + uvicorn, FastMCP for the MCP transport. HTTP/SSE MCP endpoint for agents at `/mcp/sse`; the same server also hosts the web UI, the SSE event stream for the UI, and the `/respond` endpoint used by both the web UI and ntfy action buttons. Telegram bot and ntfy client run as tasks in the same loop.

Registry is in-memory (`dict[request_id, PendingRequest]`). Restart = pending requests are lost and waiting agents time out.

## Layout

```
src/switchboard/
  __main__.py          python -m switchboard
  config.py            env-var parsing (dotenv-aware)
  registry.py          PendingRequest + Registry
  gateway.py           FastMCP + Starlette wiring, ask_human/notify_human
  web.py               /, /events SSE, /pending, /respond
  telegram_bot.py      python-telegram-bot integration
  ntfy.py              ntfy POST client
  logging_jsonl.py
static/index.html      single-page UI, vanilla JS, no build step
tests/                 pytest, pytest-asyncio, Starlette TestClient
```

## Running locally

```
pip install -e ".[dev]"
cp .env.example .env   # then fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, NTFY_URL, NTFY_TOPIC
python -m switchboard
```

Gateway comes up on `http://127.0.0.1:9876`. Point a Claude Code agent at `http://localhost:9876/mcp/sse`. Open the same URL root in a browser for the web UI.

## Testing

```
pytest                 # all tests
pytest tests/test_registry.py -v
```

Integration tests start the Starlette app in-process via `TestClient`; no external services required. Telegram and ntfy are mocked.

## Conventions

- **Python 3.11+, asyncio end-to-end.** No threads, no `run_in_executor` unless absolutely required for a blocking library.
- **Tool handlers stay thin.** `ask_human` in `gateway.py` should create the pending record, broadcast it to surfaces, and `await future` — nothing else. Complexity lives in the per-surface modules.
- **Surfaces are independently degradable.** Missing env vars disable a surface but the gateway still starts. Tests cover the degraded-startup paths.
- **No DB, no persistence.** Stick to the in-memory design unless the spec is formally revised.
- **Line endings: CRLF** (per developer's global convention). Verify with `file <path>` after editing.
- **Comments sparingly.** Explain why, not what. See the top-level `CLAUDE.md` in the developer's home if you need the full protocol.

## What belongs in CLAUDE-JOURNAL.md

Any session that produces a decision, a spec revision, or a non-trivial implementation step logs an entry. Format: `## YYYY-MM-DD — short title`, then bullets for: what changed, why, files touched, open follow-ups. Keep entries terse — this is an audit trail, not prose.
