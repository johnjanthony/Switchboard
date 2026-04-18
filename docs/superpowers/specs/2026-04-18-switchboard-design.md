# Switchboard — Design Specification

**Project:** Switchboard — Human-in-the-Loop MCP Gateway for Claude Code
**Status:** Design approved; pre-implementation
**Date:** 2026-04-18
**Supersedes:** `docs/claude-gateway-design-spec.md` (original two-option design)

---

## 1. Scope & Goals

Switchboard is a single Python process running on `127.0.0.1:9876` that exposes an MCP HTTP/SSE endpoint for Claude Code agents and a small HTTP surface for response UIs. One developer, many concurrent agents, three response surfaces chosen to match where the developer is:

- **ntfy toast** with action buttons — fast-path at-desk responses for yes/no/abort-style questions.
- **Local web UI** in a pinned browser tab — full-featured at-desk surface for free-text and multi-question queues.
- **Telegram bot** — away-from-desk (mobile) surface via reply-to-message correlation.

Any surface can resolve any pending request. The developer is never forced to use a specific surface for a given question.

### Non-Goals

- Cloud hosting (localhost only).
- Multi-user authentication (single developer, loopback binding).
- Persistence across restarts — pending requests are in-memory; restart = timeouts for waiting agents.
- Modifying Claude Code itself.
- Installing Telegram Desktop on the work computer.

### Why this shape

- The developer uses Claude Code both in VS Code (extension) and in standalone PowerShell windows. A companion **terminal pane** surface (explored in the original design) doesn't fit either workflow — the VS Code extension's chat panel isn't a terminal, and plain PowerShell CC is run single-pane. A **web UI** works identically against both host surfaces.
- ntfy toasts are already reliably delivered on this workstation; Focus Assist / corporate policies don't suppress them. So ntfy actions are load-bearing, not flaky.
- Python end-to-end was chosen over Java after verifying the Java MCP SDK is viable (`HttpServletStreamableServerTransportProvider` runs without Spring). The Python SDK's FastMCP decorator API and ~4× larger example corpus win on ergonomics for a ~1000-LOC developer tool.

---

## 2. High-level architecture

```
Claude Code agents (VS Code ext + PowerShell) ──SSE── ┐
                                                       │
                                                       ▼
                                        ┌───────────────────────────┐
                                        │   Switchboard process     │
                                        │   (Python 3.11+, asyncio) │
                                        │                           │
                            ┌── HTTP ──┤  • MCP server (FastMCP)   │
                            │           │  • HTTP routes + SSE push │
                            ▼           │  • Pending registry       │
             Pinned browser tab         │  • Telegram bot (polling) │
             (local web UI)             │  • ntfy client            │
                                        └─┬─────────────┬───────────┘
                                          │             │
                                      ntfy POST    Telegram Bot API
                                          │             │
                                          ▼             ▼
                                     ntfy toast    phone chat
                                     (at desk)     (away)
```

Single OS process. Single asyncio event loop. Starlette hosts both the MCP SSE transport (mounted at `/mcp`) and the custom HTTP routes. Uvicorn is the ASGI server. The Telegram bot and ntfy client run as asyncio tasks in the same loop.

---

## 3. MCP tools exposed

| Tool | Parameters | Returns | Blocking |
|---|---|---|---|
| `ask_human` | `question: str`, `agent_id: str`, `suggestions: list[str] \| None = None` | `str` | Yes (up to timeout) |
| `notify_human` | `message: str`, `agent_id: str` | `"ok"` | No |

Notes:

- `suggestions` max length is **3** (ntfy toast button cap). Extras are truncated with a warning log entry; the first three are used.
- `ask_human` never raises to the agent. On timeout it returns the sentinel string `"TIMEOUT: no response within {N}s"`. On unexpected internal error it returns `"ERROR: {message}"`. This keeps the agent-side contract pure-string and avoids the MCP error path.
- MCP endpoint is mounted at `/mcp/sse` (SSE stream) and `/mcp/messages` (POST). Agent MCP config:

  ```json
  {
    "mcpServers": {
      "switchboard": {
        "type": "sse",
        "url": "http://localhost:9876/mcp/sse"
      }
    }
  }
  ```

- Each agent supplies a meaningful `agent_id` (e.g. `"IR2"`, `"DMX"`) via its project CLAUDE.md instruction. Switchboard does not enforce uniqueness — `agent_id` is a human-readable label, not an identity.

---

## 4. HTTP routes

Hosted by the same Starlette app that mounts the MCP SSE transport.

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Serves the single-page web UI (HTML + vanilla JS). |
| GET | `/events` | SSE stream to the web UI: `new_request`, `resolved`, `notify` events. |
| GET | `/pending` | JSON snapshot of currently-unresolved requests. Debug and polling fallback. |
| POST | `/respond/{request_id}` | Body: plain text or JSON `{"text": "..."}`. Resolves the matching Future. Used by the web UI and by ntfy action buttons. |
| GET | `/healthz` | Returns `200 ok`. |
| (mount) | `/mcp/**` | FastMCP's SSE transport app. |

Binding: `127.0.0.1` by default. No auth — the surface is loopback-only.

---

## 5. Data model

```python
@dataclass
class PendingRequest:
    request_id: str                       # uuid4 hex, truncated to 8 chars for display
    agent_id: str
    question: str
    suggestions: list[str]                # 0..3 entries
    created_at: datetime
    future: asyncio.Future[str]
    telegram_message_id: int | None = None   # set after bot sends the outbound message
```

### Registry

- `pending: dict[str, PendingRequest]` keyed by `request_id` — the authoritative store.
- `telegram_index: dict[int, str]` — secondary index, `telegram_message_id → request_id`, for reply correlation.
- All access goes through a `Registry` class; no lock required because all mutations happen on the single asyncio loop.

### Response record (logged, not stored in-memory after resolution)

```python
@dataclass
class Response:
    request_id: str
    text: str
    source: Literal["web", "ntfy", "telegram", "timeout"]
    resolved_at: datetime
    duration_ms: int
```

---

## 6. Surface behaviors

### 6.1 Web UI (`/`)

- Single HTML page served from `static/index.html`. Vanilla JS — no framework, no build step.
- On load, opens an SSE connection to `/events`. The event stream delivers:
  - `new_request` — `{request_id, agent_id, question, suggestions, created_at}`
  - `resolved` — `{request_id, source}` (so other surfaces can remove the card)
  - `notify` — `{agent_id, message, ts}` for the activity feed
- Each pending request is rendered as a card: header `[agent_id · request_id]`, question body, suggestion buttons (if any), free-text textarea, Submit button.
- Enter submits. Shift+Enter inserts a newline. Clicking a suggestion button POSTs that suggestion as the response immediately.
- Resolved cards fade out over ~500ms.
- An activity feed below the pending cards shows the most recent 50 `notify_human` messages.
- No per-card settings, no auth, no agent filter. This is a developer tool, not a product.

### 6.2 ntfy

- On new `ask_human`, POST to `{NTFY_URL}/{NTFY_TOPIC}` with a JSON body:

  ```json
  {
    "topic": "<NTFY_TOPIC>",
    "title": "[<agent_id>] needs input",
    "message": "<first ~120 chars of question>",
    "priority": 3,
    "click": "http://localhost:9876/#req-<request_id>",
    "actions": [
      {"action": "http", "label": "<suggestion>",
       "url": "http://localhost:9876/respond/<request_id>",
       "method": "POST", "body": "<suggestion>", "clear": true}
    ]
  }
  ```

- If `NTFY_URL` or `NTFY_TOPIC` is unset, skip silently. The surface is optional.
- `notify_human` does **not** trigger ntfy — non-blocking notifications would cause toast fatigue by definition.

### 6.3 Telegram

- On new `ask_human`, the bot sends:

  ```
  🤖 [<agent_id> · <request_id>]
  <question text>

  Reply to this message to answer.
  ```

  Suggestions, if present, are appended as a bulleted list at the end of the message body — purely informational; Telegram replies are always free text (the user can type a suggestion verbatim or answer however they like).
  The `message_id` returned by `sendMessage` is stored in the registry for reply correlation.

- Incoming updates are received via long polling. If `update.message.reply_to_message.message_id` is in `telegram_index`, resolve the matching Future with `update.message.text` and send a confirmation: `✅ Answered — agent unblocked.`
- When a request is resolved by another surface, the bot edits the original outbound message to append `✅ Answered via <source>.` so the mobile view stays in sync.
- For `notify_human`: send a plain message prefixed `ℹ️ [<agent_id>] <message>`. No reply tracking.
- If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is unset, skip the Telegram surface entirely.

### 6.4 Shared resolve semantics

- First surface to POST a response wins. Every resolution broadcasts a `resolved` event on `/events` (so the web UI removes the card) and, if Telegram is enabled for the request, edits the outbound Telegram message to show which surface answered.
- The `resolved` broadcast normally arrives before a second surface tries to answer, so the stale card is already gone. In the narrow race where a second POST arrives first, the server returns `409 Conflict` and the web UI simply ignores it — the SSE event following close behind removes the card.

---

## 7. Timeouts & error handling

- Per-request timeout from `SWITCHBOARD_TIMEOUT_SECONDS` (default 3600). Implementation: `await asyncio.wait_for(pending.future, timeout=N)` inside the tool handler.
- On `TimeoutError`: remove from registry, broadcast `resolved(source=timeout)`, edit the outbound Telegram message (if Telegram is enabled for this session) to append `⏱️ Timed out.`, log a `timeout` event, return the sentinel string to the agent.
- On any other exception inside the tool handler: log a `tool_error` event, return `"ERROR: {message}"`.
- Startup-time surface failures (invalid Telegram token rejected at `getMe`, or an `NTFY_URL` that fails DNS resolution) are **warnings, not fatal**. The gateway starts with the affected surface marked disabled. Runtime errors from a given surface (e.g. ntfy POST returning non-2xx) are logged as `surface_error` and do not fail the pending request — the other surfaces still carry it. This matters: an agent should still be able to ask for input via the web UI even if Telegram is misconfigured.

---

## 8. Configuration

OS environment variables are the source of truth. If a `.env` file exists at the startup working directory, `python-dotenv` loads it into the environment before config is read. Env already set in the OS takes precedence over `.env`.

| Variable | Default | Required |
|---|---|---|
| `SWITCHBOARD_PORT` | `9876` | No |
| `SWITCHBOARD_HOST` | `127.0.0.1` | No |
| `SWITCHBOARD_TIMEOUT_SECONDS` | `3600` | No |
| `SWITCHBOARD_LOG_PATH` | `./logs/switchboard.jsonl` | No |
| `TELEGRAM_BOT_TOKEN` | — | If Telegram enabled |
| `TELEGRAM_CHAT_ID` | — | If Telegram enabled |
| `NTFY_URL` | — | If ntfy enabled |
| `NTFY_TOPIC` | — | If ntfy enabled |

There is **no** feature-flag for enabling/disabling surfaces. Presence of the credentials enables the surface. This keeps the config surface minimal.

A `.env.example` file ships with documented placeholders. `.env` itself is gitignored.

---

## 9. Logging

- JSONL file at `SWITCHBOARD_LOG_PATH`. One event per line.
- Event types: `request_created`, `request_resolved`, `notify_sent`, `timeout`, `tool_error`, `surface_error`.
- Fields per event (as applicable): `ts`, `event`, `request_id`, `agent_id`, `question_preview` (first 100 chars), `response_preview` (first 100 chars), `source`, `duration_ms`, `error`.
- Also emits human-readable lines to stderr at INFO level for live tailing during development.

---

## 10. Project layout

```
switchboard/
├── README.md
├── CLAUDE.md                       # agent orientation for this project
├── CLAUDE-JOURNAL.md                # session-by-session progress log
├── .env.example
├── .gitignore
├── pyproject.toml
├── docs/
│   ├── claude-gateway-design-spec.md                        # original, kept for history
│   └── superpowers/specs/2026-04-18-switchboard-design.md   # this spec
├── src/switchboard/
│   ├── __init__.py
│   ├── __main__.py                 # python -m switchboard
│   ├── config.py                   # env-var parsing
│   ├── registry.py                 # PendingRequest + Registry
│   ├── gateway.py                  # FastMCP setup, tool handlers, Starlette app
│   ├── web.py                      # /, /events SSE, /pending, /respond
│   ├── telegram_bot.py             # python-telegram-bot integration
│   ├── ntfy.py                     # ntfy POST client
│   └── logging_jsonl.py
├── static/
│   └── index.html                  # single-page web UI
└── tests/
    ├── test_registry.py
    ├── test_gateway_ask_human.py
    ├── test_telegram_reply_correlation.py
    ├── test_ntfy_payload.py
    └── test_timeout.py
```

---

## 11. Testing strategy

- **Unit tests** (`tests/test_registry.py`, `tests/test_ntfy_payload.py`): pending-registry add/resolve/concurrent behavior; ntfy JSON payload shape for varying suggestion counts; config loading with env + `.env`.
- **Integration tests** (`pytest-asyncio` + Starlette `TestClient`): start the app in-process, simulate a tool call from the MCP side, POST to `/respond/{id}`, assert the Future resolves with the right text and source. Fake Telegram update objects to validate reply correlation. Cover timeout, double-resolve (409), and surface-failure-on-startup scenarios.
- **Manual smoke**: `python -m switchboard` → point a Claude Code agent at `http://localhost:9876/mcp/sse` → invoke `ask_human` → verify all three surfaces (pinned browser tab, ntfy toast + action button, Telegram reply).

---

## 12. Dependencies

```toml
[project]
name = "switchboard"
requires-python = ">=3.11"
dependencies = [
  "mcp[cli]>=1.2",                 # FastMCP + SSE server transport
  "starlette>=0.37",
  "uvicorn>=0.30",
  "python-telegram-bot>=21.0",
  "httpx>=0.27",                   # ntfy POST
  "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]
```

---

## 13. Open items (deferred to implementation, not blockers)

- **ntfy URL + topic:** developer will confirm during implementation. Design slots into env vars (`NTFY_URL`, `NTFY_TOPIC`).
- **Telegram bot token + chat_id:** developer will look these up (credentials reused from the AgentOrchestrator bot `AgentOrchestratorBot`). Design slots into env vars.
- **PyInstaller packaging:** not in v1. If Windows startup via Task Scheduler becomes clunky, revisit.
- **Windows auto-start:** documented in README via Task Scheduler; not automated by the installer.

---

## 14. Security posture

- Loopback binding (`127.0.0.1`) by default — no network exposure.
- No authentication on any HTTP route. Reasoning: the only other caller on the loopback interface is the developer's browser, their ntfy client (both local), and their own Claude Code agents. Exposing this to the LAN is explicitly not supported.
- Telegram polls outbound over TLS; bot token is the only secret on the Telegram path.
- ntfy POSTs outbound over whatever transport the developer's ntfy is configured for.
- `ask_human` does no sanitization on the question string — it's trusted input from the developer's own agents. The web UI must render question text as text, never as HTML (basic XSS hygiene for the rare case where a question contains angle brackets).

---

*Next step: produce the implementation plan via the superpowers `writing-plans` skill.*
