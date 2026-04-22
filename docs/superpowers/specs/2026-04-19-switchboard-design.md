# Switchboard — Design Specification (Superpowered)

**Date:** 2026-04-19
**Status:** Canonical (replaces all prior specs)
**Scope:** MVP (Telegram Backend) + Android Phase Logic

---

## 1. Executive Summary

Claude Code agents need a way to request human judgment when John is away from his computer. Switchboard is a local MCP server that routes these requests to John's phone (via Android or Telegram) and blocks the agent until a response is received.

---

## 2. Problem Statement

Claude Code agents are capable of running long, complex, multi-step tasks autonomously. But some decisions — overwriting a file, running a migration, choosing between two approaches — genuinely require human judgment before proceeding. Without a mechanism for mid-task human input, agents either guess, abort, or require constant supervision.

Switchboard gives agents a reliable way to ask — and gives John a single phone endpoint to respond from, regardless of which backend is enabled.

---

## 3. Usage Model

### 3.1 Normal (at desk) workflow

John works interactively with Claude Code via the VS Code extension as usual. Switchboard is available but largely idle. If the agent needs input it asks normally in the VS Code chat UI.

### 3.2 Away workflow

When stepping away from the desk, John tells the agent:

> "I'm stepping away. Use the `ask_human` MCP tool for any questions or decisions that would normally require my input. Do not wait for responses in this chat. I'll respond via my phone."

John then either:

- Leaves the VS Code session running, or
- Closes VS Code and resumes the session in a terminal:

  ```bash
  claude --resume --dangerously-skip-permissions
  ```

The agent continues working. Any input requests are routed through Switchboard to John's phone. John responds from his phone. On return, John tells the agent they are back and normal interaction resumes.

### 3.3 On `--dangerously-skip-permissions`

Resuming the session with `--dangerously-skip-permissions` disables permission prompts for **every** tool call the agent makes during the away session — file edits, bash commands, git operations, everything. This is not scoped to `ask_human` routing.

This is an accepted tradeoff: the whole point of away mode is that the agent runs unsupervised, so permission prompts would be unanswerable anyway. But it is a bigger hammer than Switchboard alone, and John should be deliberate about when to use it. If the agent is working on something John would not trust it to do unsupervised, away mode is the wrong tool — Switchboard does not change that calculus.

### 3.4 Enforcement note

Instructing the agent to use `ask_human` is best-effort — it relies on model instruction-following rather than hard enforcement. In practice, an agent deep in a task is unlikely to break this pattern, but it is not guaranteed. If the agent stalls waiting for terminal/chat input while John is away, the failure mode is a paused task rather than an incorrect action. This is acceptable for the initial implementation. A more robust SDK-based enforcement approach can be considered if this proves to be a meaningful issue in practice.

---

## 4. Architecture

```text
Claude Code Agent 1 ──SSE──┐
Claude Code Agent 2 ──SSE──┤──► Switchboard MCP Server ◄──► Messenger Backend
Claude Code Agent N ──SSE──┘     (Python asyncio)               (Pluggable)
                                   localhost:9876
```

All agents connect to a single shared Switchboard instance. Each pending request is tracked independently — multiple agents can be blocked simultaneously, each waiting for its own response.

---

## 5. Components

### 5.1 Switchboard MCP Server

- **Language:** Python 3.11+
- **Transport:** HTTP/SSE (allows multiple simultaneous agent connections)
- **Pending request tracking:** `dict[str, PendingRequest]` keyed by short UUID (`asyncio.Future` held inside each record)
- **Startup:** run manually or via a launcher script; must be running before agents start

### 5.2 MCP Tools

| Tool | Parameters | Returns | Blocking |
|---|---|---|---|
| `ask_human` | `question: str`, `agent_id: str`, `format: str = "plain"`, `suggestions: list[str] \| None = None` | `str` (the response) | Yes |
| `notify_human` | `message: str`, `agent_id: str`, `format: str = "plain"` | `"ok"` | No |
| `send_document_human` | `path: str`, `agent_id: str`, `caption: str \| None = None` | `"ok"` or `"ERROR: ..."` | No |

**`ask_human`** — blocks the calling agent until John responds via the messenger backend. The agent should use this for any decision that requires human input. Pass `format="markdown"` for rich formatting; `suggestions` renders tap-able reply buttons on the Android client.

**`notify_human`** — fires a backend message and returns immediately. The agent should use this for status updates that don't require a response (e.g. "starting migration", "task complete"). Pass `format="markdown"` for rich formatting.

**`send_document_human`** — delivers a file to John's phone. Fire-and-forget. `path` must resolve within the project directory (no `..` traversal); max 5 MB; sensitive filenames (`.env`, `*.pem`, `*token*`, etc.) are rejected.

**`format` parameter:** `"plain"` (default) passes text through as-is. `"markdown"` renders the message body using standard Markdown syntax on the Android client (bold, italic, inline code, code blocks, links). Note: the Telegram backend only handles `format="html"` — if Telegram is re-enabled, `telegram.py` will need updating to recognise `"markdown"`.

### 5.3 Messenger Backend

The messenger surface is abstracted behind an interface so the transport can evolve without touching the gateway core. Supporting both a native Android client and Telegram.

**Interface** (`server/messenger.py`):

```python
class MessengerBackend(ABC):
    async def send_question(self, request_id: str, agent_id: str, question: str) -> CorrelationToken:
        """Deliver question to the phone. Return token for correlation."""

    async def send_notification(self, agent_id: str, message: str) -> None:
        """Deliver fire-and-forget message."""

    async def send_timeout_followup(self, request_id: str, agent_id: str, timeout_seconds: int, correlation: CorrelationToken) -> None:
        """Inform John a pending question has timed out."""

    async def send_resolution_confirmation(self, request_id: str, agent_id: str, correlation: CorrelationToken) -> None:
        """Confirm to John that their response was received."""

    async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
        """Yield responses from John as they arrive."""
```

---

## 6. Backend Options

### 6.1 Telegram

- **Transport:** Long-polling `getUpdates` (standard bot API).
- **Correlation:** Uses Telegram's `reply_to_message`.
    - `send_question` sends the question and records the resulting `message_id`.
    - `poll_responses` ignores any message that is not a reply to one of its own sent questions.

### 6.2 Android + Firebase

**Shipped.** Native Android app (Kotlin/Compose) is the active primary backend.

- **Transport:** Firebase Realtime Database.
- **UI:** Scrollable tab strip — one tab per `agent_id`. Tabs are created automatically when a new agent sends its first message. Dark theme: black message bubbles, grey background. Markdown rendering via Markwon (bold, italic, cyan inline code, code blocks with preserved line breaks).
- **Question flow:** Server writes to `/questions/$request_id`. App listens, renders question in the agent's tab with an inline reply field and optional suggestion buttons. User types or taps a suggestion; app writes to `/responses/$request_id`. Server resolves the waiting future.
- **Notifications:** Server sends FCM push notifications for questions (IMPORTANCE_HIGH — heads-up banner with sound), status updates, and documents (IMPORTANCE_DEFAULT). Tapping a notification opens the app directly to the correct agent tab. `POST_NOTIFICATIONS` permission requested at first launch.
- **Documents:** `send_document_human` uploads the file to Firebase Storage and writes metadata to `/documents`. App renders a document bubble with an Open button.
- **Session management:** Sessions are tracked in `/sessions/$agent_id`. The app only displays agents with `state: "open"`. Closing a tab marks the session closed and answers any pending questions with a "back at desk" message.
- **Spawn:** The `+` button in the app writes a `/spawn` command to `/commands`, which the server picks up and executes via the `SwitchboardSpawn` scheduled task.

---

## 7. Tool Logic

- **`agent_id`** — a short, human-meaningful label that appears in every backend message so John knows which agent is calling.
    1. **John-provided label.** When John hands off into away mode, they may specify an explicit label (e.g. "call yourself IR2").
    2. **Agent-derived label.** If John has not specified a label, the agent selects a short (1-3 word) label based on the current task (e.g. `DMXRefactor`).
- **Timeouts**
    - Default timeout is **24 hours** (86400s), configurable. Away sessions are expected to span long periods.
    - When a timeout occurs:
        1. Gateway sends a "Timed out after 24h" follow-up message.
        2. Registry resolves the agent's future with the sentinel string `"__TIMEOUT__"`.
        3. Registry cleans up the short UUID and correlation token.
    - This ensures John coming back to his phone is not confused about whether the agent is still waiting.
- **Sentinel returns**
    - The skill instructs agents to treat a `"__TIMEOUT__"` response as an instruction to pause and wait for John to return before continuing.

---

## 8. Logging

Every tool call and resolution is logged to a local JSONL file (`logs/switchboard.jsonl`):

- `request_created`: Short ID, agent ID, question text, timestamp.
- `request_resolved`: Short ID, response text, duration, source.
- `request_timed_out`: Short ID, duration.
- `notification_sent`: Agent ID, message text.
- `document_sent`: Agent ID, resolved path, size_bytes, sha256, caption.

---

## 9. Configuration

Configuration via environment variables (with `.env` file fallback):

- `SWITCHBOARD_PORT`: Default 9876.
- `SWITCHBOARD_TIMEOUT_SECONDS`: Default 86400.
- `SWITCHBOARD_LOG_PATH`: Default `./logs/switchboard.jsonl`.
- `SWITCHBOARD_ENABLE_TELEGRAM`: Boolean toggle.
- `SWITCHBOARD_ENABLE_ANDROID`: Boolean toggle.

---

## 10. Design Decisions

### Messenger backend abstraction

While multiple backends exist, the core tool logic does not know which is being used. The `MessengerBackend` ABC allows the gateway to be transport-agnostic.

### Short UUIDs for request IDs

`ask_human` generates a short (4-6 character) alphanumeric ID for each request. This ID is prefixed to messages. While backend-specific correlation may use other tokens, the short ID is invaluable for audit logging and human-readable tracking.

### SSE for MCP transport

FastMCP/FastAPI's SSE transport is used because it supports long-lived connections from multiple agents simultaneously. Unlike stdio, SSE allows the server to remain up while agents come and go.

---

## 11. Project Structure

```text
server/
  main.py          # Entry point — wires config, registry, backend, MCP, uvicorn
  config.py        # Env-based Config loader
  registry.py      # PendingRequest + Registry
  messenger.py     # MessengerBackend ABC + IncomingResponse
  telegram.py      # Telegram implementation
  android.py       # Android/Firebase implementation
  firebase.py      # Firebase admin logic
  gateway.py       # Tool handlers (ask_human, notify_human, send_document_human) + dispatch loops
  spawn.py         # Telegram/Firebase-triggered Claude Code session spawner
  logging_jsonl.py # JSONL audit log
skill/
  SKILL.md         # MCP skill instructions for the agent
android/
  app/src/main/
    AndroidManifest.xml
    java/io/github/johnjanthony/switchboard/
      MainActivity.kt        # Compose UI — tabs, chat view, message bubbles, spawn dialog
      MainViewModel.kt       # Firebase listeners, question/notification/document state
      fcm/
        SwitchboardFirebaseMessagingService.kt  # Push notifications
      network/
        ApiService.kt        # Question data class
scripts/
  install-service.ps1        # NSSM service install
  uninstall-service.ps1
  restart-service.ps1        # Stop + pytest gate + start
  register-spawn-task.ps1
  spawn-launcher.ps1
tests/             # Pytest suite
  test_config.py
  test_registry.py
  test_gateway.py
  test_telegram.py
```

---

## 12. Security Considerations

- **Single user** — `TELEGRAM_CHAT_ID` / Firebase auth restricts access to only John.
- **Local binding** — MCP server binds to `127.0.0.1` by default. No external auth needed for local agent connections.
- **Input trust** — `ask_human` question strings are trusted; John's responses are trusted. No complex sanitization.
