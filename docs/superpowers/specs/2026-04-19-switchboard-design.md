# Switchboard — Design Specification

**Project:** Switchboard — Human-in-the-Loop MCP Gateway for Claude Code
**Status:** Design approved; ready for implementation planning
**Version:** 2.1 (canonical)
**Date:** 2026-04-19
**Supersedes:** `docs/superpowers/specs/2026-04-18-switchboard-design.md` (three-surface design), `docs/design-spec-v2.1.md` (pre-consolidation draft at a non-canonical path), `docs/claude-gateway-design-spec.md` (original two-option exploration)

This is the canonical design for Switchboard. If any earlier spec disagrees with this document, this document wins.

---

## 1. Overview

Switchboard is a locally-hosted MCP server that provides a human-in-the-loop input gateway for Claude Code agents. It allows one or more Claude Code agents running on the same Windows workstation to pause mid-task and request human input via an `ask_human` MCP tool call. The agent blocks deterministically until a response is provided via the configured messenger backend (Telegram in v1), then continues.

---

## 2. Problem Statement

Claude Code agents are capable of running long, complex, multi-step tasks autonomously. But some decisions — overwriting a file, running a migration, choosing between two approaches — genuinely require human judgment before proceeding. Without a mechanism for mid-task human input, agents either guess, abort, or require constant supervision.

Switchboard gives agents a reliable way to ask — and gives the developer a single mobile endpoint to respond from, regardless of how many agents are running.

---

## 3. Usage Model

### 3.1 Normal (at desk) workflow

The developer works interactively with Claude Code via the VS Code extension as usual. Switchboard is available but largely idle. If the agent needs input it asks normally in the VS Code chat UI.

### 3.2 Away workflow

When stepping away from the desk, the developer tells the agent:

> "I'm stepping away. Use the `ask_human` MCP tool for any questions or decisions that would normally require my input. Do not wait for responses in this chat. I'll respond via Telegram."

The developer then either:

- Leaves the VS Code session running, or
- Closes VS Code and resumes the session in a terminal:

  ```bash
  claude --resume --dangerously-skip-permissions
  ```

The agent continues working. Any input requests are routed through Switchboard to Telegram. The developer responds from their phone. On return, the developer tells the agent they are back and normal interaction resumes.

### 3.3 On `--dangerously-skip-permissions`

Resuming the session with `--dangerously-skip-permissions` disables permission prompts for **every** tool call the agent makes during the away session — file edits, bash commands, git operations, everything. This is not scoped to `ask_human` routing.

This is an accepted tradeoff: the whole point of away mode is that the agent runs unsupervised, so permission prompts would be unanswerable anyway. But it is a bigger hammer than Switchboard alone, and the developer should be deliberate about when to use it. If the agent is working on something the developer would not trust it to do unsupervised, away mode is the wrong tool — Switchboard does not change that calculus.

### 3.4 Enforcement note

Instructing the agent to use `ask_human` is best-effort — it relies on model instruction-following rather than hard enforcement. In practice, an agent deep in a task is unlikely to break this pattern, but it is not guaranteed. If the agent stalls waiting for terminal/chat input while the developer is away, the failure mode is a paused task rather than an incorrect action. This is acceptable for the initial implementation. A more robust SDK-based enforcement approach can be considered if this proves to be a meaningful issue in practice.

---

## 4. Architecture

```text
Claude Code Agent 1 ──SSE──┐
Claude Code Agent 2 ──SSE──┤──► Switchboard MCP Server ◄──► Messenger Backend
Claude Code Agent N ──SSE──┘     (Python asyncio)               (v1: Telegram)
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
| `ask_human` | `question: str`, `agent_id: str` | `str` (the response) | Yes |
| `notify_human` | `message: str`, `agent_id: str` | `"ok"` | No |

**`ask_human`** — blocks the calling agent until the developer responds via the messenger backend. The agent should use this for any decision that requires human judgment.

**`notify_human`** — fires a backend message and returns immediately. The agent should use this for status updates that don't require a response (e.g. "starting migration", "task complete").

### 5.3 Messenger Backend

The messenger surface is abstracted behind an interface so the transport can evolve without touching the gateway core. v1 ships a Telegram backend; a native Android client backed by Firebase Realtime Database is a planned future phase (see §7 and §10).

**Interface** (`server/messenger.py`):

```python
class MessengerBackend(ABC):
    async def send_question(
        self, request_id: str, agent_id: str, question: str
    ) -> CorrelationToken:
        """Deliver the question. Return whatever the backend needs later
        to match an incoming response to this request_id."""

    async def send_notification(self, agent_id: str, message: str) -> None:
        """Fire-and-forget status update; no reply tracking."""

    async def send_timeout_followup(
        self, request_id: str, agent_id: str, timeout_seconds: int,
        correlation: CorrelationToken,
    ) -> None:
        """Inform the developer a pending question has timed out."""

    async def send_resolution_confirmation(
        self, request_id: str, agent_id: str,
        correlation: CorrelationToken,
    ) -> None:
        """Confirm to the developer that their response was received."""

    async def poll_responses(self) -> AsyncIterator[IncomingResponse]:
        """Yield IncomingResponse(request_id, text) as responses arrive."""
```

`CorrelationToken` is backend-specific: for Telegram it's the outbound `message_id` (int); for a future Firebase backend it might be a document path or FCM message ID. The gateway stores it opaquely in the pending record and hands it back on resolution calls.

#### 5.3.1 v1 concrete implementation — Telegram (`server/telegram.py`)

- Receives forwarded questions from the gateway.
- Each message includes the agent label and a short request ID.
- Developer replies using Telegram's reply-to-message feature — no special syntax required.
- The Telegram backend correlates the reply to the correct pending Future via `message.reply_to_message.message_id`.
- Confirms to the developer which request was resolved once a response is received.
- Transport: long-polling via `getUpdates` (polling is recommended for v1 — no public endpoint or tunnel required).

---

## 6. Request Lifecycle

```text
Agent calls ask_human(question="Overwrite foo.java?", agent_id="IR2")
    │
    ▼
Switchboard assigns request_id "a3f1", stores PendingRequest in dict
    │
    ▼
MessengerBackend.send_question() — v1 Telegram:
    "[IR2 | a3f1] Overwrite foo.java?"
    returns message_id as CorrelationToken
    │
    ▼
Agent blocks — awaiting Future resolution (default timeout: 24h)
    │
Developer replies to the message on phone
    │
    ▼
MessengerBackend.poll_responses() yields IncomingResponse(request_id="a3f1", text="yes")
Gateway resolves the Future for "a3f1"
    │
    ▼
MessengerBackend.send_resolution_confirmation("a3f1", "IR2", token)
    ─► "✅ [IR2 | a3f1] answered"
    │
    ▼
ask_human returns response string to agent
Agent continues
```

---

## 7. Agent Configuration

Each Claude Code project that uses Switchboard adds the following to its MCP configuration:

```json
{
  "mcpServers": {
    "switchboard": {
      "type": "sse",
      "url": "http://localhost:9876/sse"
    }
  }
}
```

---

## 8. Skill

Switchboard ships as a Claude Code skill. The skill owns all usage instructions — how and when to call `ask_human` and `notify_human`, what to do on timeout, and how to behave in away mode. Instructions are defined once and propagate automatically to every project that has the skill installed, without copying boilerplate into each project's `CLAUDE.md`.

The skill file lives at `~/.claude/skills/switchboard/SKILL.md` (or equivalent path per local Claude Code skill configuration).

### 8.1 `agent_id` selection

The `agent_id` is a short, human-meaningful label that appears in every backend message so the developer can tell at a glance which agent is asking. It is not an identity and uniqueness is not enforced — it is purely for labeling.

The skill instructs the agent to obtain `agent_id` in this order of preference:

1. **Developer-provided label.** When the developer hands off into away mode, they may specify an explicit label (e.g. "call yourself IR2 for this session" or "label these as migration-work"). If one is given, use it for every `ask_human` / `notify_human` call during the away session.
2. **Agent-derived label.** If the developer has not specified a label, the agent selects a short (1–3 word) label based on the current task or topic at the moment of the first `ask_human` call — e.g. `DMXRefactor`, `IR2Migration`, `DocGen`. The agent then uses that same label for every subsequent call in the session.

The label is passed explicitly as the `agent_id` parameter on each call. The server does not attempt to introspect session state on the agent's behalf.

---

## 9. Timeout Behavior

- Default timeout is **24 hours** (86400s), configurable. Away sessions are expected to span long periods and the developer may not see their phone for many hours at a time.
- If no response arrives within the timeout, `ask_human` returns the sentinel string `"__TIMEOUT__"` so the agent can handle it gracefully rather than hanging indefinitely.
- On timeout, the messenger backend sends a **follow-up message** linked to the original:

  ```text
  ⏱️ [IR2 | a3f1] timed out after 24h. Agent received timeout signal.
  ```

  This ensures the developer coming back to the phone is not confused about whether the agent is still waiting on a stale question.

- The skill instructs agents to treat a `"__TIMEOUT__"` response as an instruction to pause and wait for the developer to return before continuing — not to guess and continue.

---

## 10. Logging

Switchboard writes a JSONL audit log at `./logs/switchboard.jsonl` (one event per line). This is lightweight (~20 LOC) and invaluable when correlation misfires or a request goes missing.

Event types:

- `request_created` — new `ask_human` call received
- `request_resolved` — response matched, Future resolved
- `notify_sent` — `notify_human` fired
- `timeout` — request hit the timeout window
- `tool_error` — unexpected exception in a tool handler

Each event includes: `ts`, `event`, `request_id`, `agent_id`, `question_preview` (first 100 chars) or `message_preview`, `response_preview` (first 100 chars) where applicable, `source` (`"telegram"` in v1), and `duration_ms` on resolution.

Human-readable lines are also emitted to stderr at INFO level for live tailing during development.

---

## 11. Project Structure

```text
switchboard/
├── README.md
├── CLAUDE.md
├── CLAUDE-JOURNAL.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── docs/
│   ├── superpowers/specs/2026-04-19-switchboard-design.md   # this spec (canonical)
│   └── ... (earlier specs, retained as history)
├── server/
│   ├── __init__.py
│   ├── main.py                 # entry point (python -m server)
│   ├── config.py               # env-var parsing (dotenv-aware)
│   ├── gateway.py              # FastMCP tool handlers + pending registry
│   ├── registry.py             # PendingRequest dataclass + Registry
│   ├── messenger.py            # MessengerBackend ABC + factory
│   ├── telegram.py             # Telegram MessengerBackend implementation
│   └── logging_jsonl.py        # JSONL audit log
├── skill/
│   └── SKILL.md                # installed into ~/.claude/skills/switchboard/
├── tests/
│   ├── test_registry.py
│   ├── test_gateway_ask_human.py
│   ├── test_telegram_correlation.py
│   └── test_timeout.py
└── logs/
    └── switchboard.jsonl
```

---

## 12. Design Decisions

### 12.1 MCP server vs. direct Telegram script

A simpler alternative was considered: a standalone Python script that the agent calls via the Bash tool, which sends a Telegram message and long-polls for a reply. This would eliminate the MCP server entirely.

MCP was chosen instead for the following reasons:

- **Tool visibility** — MCP tools appear explicitly in the agent's tool list at session start. A Bash script is a convention described in the skill; the agent has to be told it exists rather than discovering it as a registered capability.
- **Bash tool dependency** — the script approach requires Bash tool access. If a session is started with restricted `--allowedTools`, script calls fail silently. MCP tools are always available if the server is running.
- **Blocking semantics** — with the script approach, a long-polling Bash call hangs visibly in the agent's tool execution. MCP blocking is at the protocol level where it belongs.
- **Extensibility** — adding future response surfaces is straightforward with an MCP server as the central hub. The script approach would require rebuilding toward MCP at that point anyway.

### 12.2 Telegram as the only response surface (away-mode framing)

At-desk interaction already has a human-input channel: the VS Code extension chat UI. Switchboard exists specifically for the case where that channel is idle because the developer has stepped away. Under that framing, additional at-desk surfaces (web UI, ntfy toasts, companion terminal pane) solve a problem that does not exist — the developer is not there to see them. Telegram-only keeps the scope honest.

### 12.3 Messenger backend abstraction

Telegram is the right backend for v1 — zero infrastructure, reachable from any phone, correlation via reply-to-message is already the cleanest pattern available. The developer's longer-term direction is a native Android client backed by Firebase Realtime Database, reusing the `android-remote/` work from the AgentOrchestrator / Forge project. Rather than retrofit that later, the messenger surface is abstracted behind a `MessengerBackend` ABC from day one. Rationale:

- The abstraction is cheap upfront (one ABC plus one concrete impl). Retrofitting after v1 ships is disruptive — the Telegram-specific `message_id` correlation assumption is already the one piece of the design that doesn't generalize.
- The gateway, registry, skill, logging, and MCP tool contract (`ask_human` / `notify_human`) are independent of transport and do not change across backends.
- v1 stays simple: one concrete implementation (`TelegramBackend`), one selector in `main.py` (no dynamic loading), no extra configuration surface for the developer.
- When a Firebase/Android backend is added, it is a new file (`server/firebase.py`) implementing the same ABC and a one-line selector change in `main.py`.

---

## 13. Out of Scope (Initial Implementation)

The following were considered and deferred:

- **Companion terminal pane** — a TUI script for at-desk terminal responses. Deferred pending validation that best-effort instruction following is insufficient.
- **Desktop notification + dialog** — Windows toast + tkinter input dialog as an at-desk response surface. Same deferral rationale.
- **SDK wrapper enforcement** — programmatic I/O control to hard-enforce `ask_human` routing. Available as a future upgrade path if instruction-following proves unreliable.
- **ntfy integration** — considered for desktop notifications; not needed for initial scope.
- **Inline Telegram keyboard buttons** — yes/no quick replies. Nice to have, not required for MVP.
- **Web UI** — pinned-tab response surface. Obviated by the away-mode framing.
- **Native Android client + Firebase backend** — planned phase 2. The `android-remote/` Android app from AgentOrchestrator (Firebase Realtime Database, FCM push) will be adapted to Switchboard's schema and plugged into the gateway as a second `MessengerBackend` implementation. The abstraction defined in v1 is the load-bearing preparation for this phase.

---

## 14. Open Questions for Implementation

The following decisions can be finalized during implementation:

1. **Telegram library** — raw `httpx` calls vs `python-telegram-bot`. The library has a mature async API and handles reconnection/backoff, but adds a dependency. Raw httpx is ~80 LOC for v1's needs. To be decided during implementation.
2. **Configuration** — Telegram bot token and chat ID storage. The developer prefers OS-level environment variables; `.env` file is the fallback if env is not already populated. `python-dotenv` handles the precedence.
3. **Multi-user support** — currently assumes a single developer/chat ID. Multi-user routing is out of scope for MVP.

---

## 15. Security Posture

- Loopback binding (`127.0.0.1`) by default. No network exposure.
- No authentication on any HTTP route. Reasoning: the only local callers are the developer's own Claude Code agents on the loopback interface. Exposing this to the LAN is explicitly unsupported.
- Telegram traffic is outbound over TLS; the bot token is the only secret on the Telegram path.
- The gateway performs no sanitization on question/message strings — they are trusted input from the developer's own agents.

---

*Next step: produce the implementation plan via the superpowers `writing-plans` skill.*
