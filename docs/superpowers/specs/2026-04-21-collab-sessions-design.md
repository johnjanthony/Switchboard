# Switchboard Collab Sessions Design

**Date:** 2026-04-21
**Status:** Approved

---

## 1. Overview

Collab sessions extend Switchboard with two-agent peer collaboration. Two Claude Code instances are spawned into the same project, communicate turn-by-turn through a new MCP tool, and are monitored by Switchboard acting as orchestrator. John can watch the transcript on Android, inject messages at any time, and is clearly notified when an agent is waiting for his direct response.

This design draws from the Forge (AgentOrchestrator) project's peer debate pattern but replaces Forge's CLI subprocess loop with Switchboard's existing MCP tool infrastructure.

---

## 2. New MCP Tool

### `message_and_await_agent`

```python
message_and_await_agent(session_id: str, agent_id: str, message: str | None = None) -> str
```

- `session_id` — the collab session this agent belongs to
- `agent_id` — the calling agent's own ID (e.g. `myproject-abc123-1`)
- `message` — optional outbound message to the other agent; omitted on Agent 2's first call

**Behavior:**
1. Look up `sessions[session_id]`. Return `"ERROR: session not found"` if missing or `agent_id` is not a member.
2. If `message` is provided: push `{speaker, message, timestamp}` onto the session's message queue; if `relay=True`, forward to Android with speaker attribution; append to `session.transcript`.
3. `await asyncio.wait_for(queue.get(), timeout=config.timeout_seconds)` — same 24-hour default as `ask_human`.
4. Return the received message text, or `"__TIMEOUT__"` sentinel on timeout.

The blocking semantics enforce turn-taking naturally: Agent A sends and blocks; Agent B's call resolves, B processes and sends back; A's call resolves. No server-side turn enforcement needed.

Human injections arrive through the same queue — agents receive them identically to partner messages.

---

## 3. CollabSession Data Model

**New module: `server/collab.py`**

```python
@dataclass
class CollabSession:
	session_id: str                  # e.g. "myproject-abc123"
	agent_ids: tuple[str, str]       # ("myproject-abc123-1", "myproject-abc123-2")
	task: str
	relay: bool
	created_at: datetime
	message_queue: asyncio.Queue     # shared; both agents pull from this
	transcript: list[dict]           # {speaker, message, timestamp}
```

**Registry additions in `server/registry.py`:**

```python
_sessions: dict[str, CollabSession]    # keyed by session_id
_agent_to_session: dict[str, str]      # agent_id → session_id; for ask_human / notify_human routing
```

`message_and_await_agent` uses the direct `sessions[session_id]` lookup (caller provides `session_id` explicitly). `ask_human` and `notify_human` only receive `agent_id`, so they use `_agent_to_session` to determine whether to write to a session Firebase node or the standard `questions/{agent_id}` node. Both indexes are populated at session creation and cleared on session removal.

On session creation, a `collab-sessions.json` sidecar is written to disk for restart recovery (see §7).

---

## 4. Spawn Flow

### Command syntax

```
/spawn [project] [task] --agents=2 [--relay]
```

- `--agents=2` activates the collab path (default `--agents=1` preserves existing single-agent behaviour)
- `--relay` enables live transcript relay to Android; without it, Android is silent until an agent calls `ask_human`
- `task` is optional; default prompt is used if omitted (see §5)

### ID assignment

```
session_id = f"{project}-{short_uuid()}"    # e.g. "myproject-abc123"
agent_1_id = f"{session_id}-1"
agent_2_id = f"{session_id}-2"
```

### `spawn-pending.json` (extended)

```json
{
	"session_id": "myproject-abc123",
	"relay": true,
	"agents": [
		{"agent_id": "myproject-abc123-1", "prompt": "...", "project_path": "C:\\Work\\myproject"},
		{"agent_id": "myproject-abc123-2", "prompt": "...", "project_path": "C:\\Work\\myproject"}
	]
}
```

`spawn-launcher.ps1` reads this and opens two Windows Terminal tabs, one per agent entry.

### Rate limiting

A collab spawn is treated as a single rate-limit event — the same 60-second window as a single-agent spawn. Both agents are launched together; the rate limit is not applied per agent slot.

---

## 5. Agent System Prompt

Injected into each agent's Claude Code invocation at spawn time. Adapted from Forge's `PromptConstants.SYSTEM_RULES`.

**Agent 1 prompt:**
```
You are Agent 1 in a two-agent collaborative session.

Session ID: {session_id}
Your agent ID: {agent_1_id}
John is away. All human communication MUST go through ask_human / notify_human.

COLLABORATION RULES:
1. Use message_and_await_agent(session_id, agent_id, message) to communicate with your
   partner. Always pass your own agent_id as the second argument.
2. Speak only to your partner — not to John — unless using ask_human or notify_human.
3. No meta-commentary. Respond with content directly.
4. Critically review your partner's proposals. Be specific.
5. Your goal is to reach consensus. When you believe consensus is reached, call ask_human
   to confirm with John before proceeding further.
6. If debate becomes unproductive, call ask_human to report the deadlock.
7. After making changes, verify them with appropriate tools before claiming completion.
8. If message_and_await_agent returns "__TIMEOUT__", call ask_human to check in with John.
9. If message_and_await_agent returns an error, call ask_human immediately.

TASK:
{task}
```

**Agent 2 prompt:** identical except the preamble adds:
> *"Your partner will send the first message. Begin by calling `message_and_await_agent(session_id, agent_id)` with no message argument to listen."*

### Default task prompt (when none provided)

> *"Perform a comprehensive technical review of this codebase. Identify architectural weaknesses, potential bugs, and high-to-medium priority areas for improvement. Debate these points critically with your partner until you reach consensus on what needs to change, then implement those changes and verify them."*

---

## 6. Firebase Schema + Android

### Firebase

Session traffic writes to a dedicated session node. The `session_id` acts as the chat identity, the same way `agent_id` does for single-agent sessions.

```
sessions/{session_id}/meta
  agent_ids:   ["myproject-abc123-1", "myproject-abc123-2"]
  relay:       true
  task:        "review auth module"
  created_at:  "2026-04-21T..."

sessions/{session_id}/messages/{message_id}
  speaker:     "myproject-abc123-1" | "myproject-abc123-2" | "human"
  type:        "collab" | "ask_human" | "inject"
  content:     "..."
  request_id:  "abc8def2"    ← present only when type = "ask_human"
  timestamp:   "..."

sessions/{session_id}/inject_queue/{id}
  content:     "..."
  timestamp:   "..."
```

`ask_human` calls from within a collab session write to the session messages node (not `questions/{agent_id}`). The `request_id` is still created in the registry for correlation; Firebase just stores it alongside the message for the Android client to reference.

Human injections write to `inject_queue`. Switchboard polls this and pushes into the in-memory session message queue.

### Android

**Tab creation:** `MainViewModel` listens on `sessions/`. When a new session node appears, a tab is created keyed by `session_id` and labelled with the project name.

**Speaker attribution:** `meta/agent_ids[0]` → "Agent 1", `meta/agent_ids[1]` → "Agent 2", `"human"` → user bubble style. No hardcoding.

**Message rendering:**
- `type = "collab"` → normal attributed bubble
- `type = "ask_human"` → distinct bubble (accent border/colour); triggers reply UI
- `type = "inject"` → user bubble

**Ask_human reply UI (when `type = "ask_human"` message arrives):**
1. Sticky banner above compose box: *"Agent 1 is waiting for your reply"*
2. Quoted preview of the specific question anchored in the compose area (WhatsApp-style); tapping scrolls to that bubble in the transcript
3. Compose placeholder changes to *"Reply to Agent 1..."*
4. Sending resolves via `responses/{request_id}` (existing path) — not the session queue
5. On resolution, banner and quote preview clear; compose reverts to inject mode

**Inject mode (no ask_human pending):**
- Compose placeholder: *"Inject into conversation..."*
- Compose box and send button always visible
- Send writes to `sessions/{session_id}/inject_queue`

**Simultaneous ask_human calls:** unlikely in a turn-based session but handled. The Android app maintains a list of pending `ask_human` entries for the session (ordered by arrival). It displays the banner and quote preview for the first pending entry; on resolution it advances to the next. John sees one at a time.

---

## 7. Error Handling

| Scenario | Behaviour |
|---|---|
| Both agents timeout (24h) | Each gets `"__TIMEOUT__"` sentinel; system prompt instructs them to call `ask_human`. Switchboard fires `notify_human`: *"Collab session X — no activity for 24h."* |
| One agent dies unexpectedly | Surviving agent hits 24h timeout, falls through to `ask_human`. John sees the timeout notification. |
| Session not found | Tool returns `"ERROR: session not found"`. System prompt instructs agents to call `ask_human` on any tool error. |
| `ask_human` while partner is waiting | Normal. Both have independent 24h windows. Partner's wait resolves when the first agent finishes with John and calls `message_and_await_agent`. |
| Switchboard restart | Unlike single-agent sessions (which survive fast restarts via Claude Code's ~31s auto-reconnect), collab sessions do not recover. Even if the SSE connection re-establishes, the in-memory `CollabSession` is gone — agents call `message_and_await_agent` and receive `"ERROR: session not found"`, breaking the collaboration regardless of reconnect speed. On startup, Switchboard reads `collab-sessions.json` (written at session creation) and fires `notify_human` for each listed session: *"Switchboard restarted. Collab session X was lost — agents will time out."* The sidecar is then cleared. It accumulates entries across spawns and is only cleared on startup. |

---

## 8. What Is Not In Scope

- **Git worktree isolation** — agents share the project directory. No per-session worktrees. (Forge feature; not needed for the Switchboard peer debate model.)
- **Auto-commit / auto-PR on consensus** — John confirms consensus via `ask_human`; he decides what happens next.
- **More than two agents** — `--agents=N` flag parses N but only N=2 is implemented. Groundwork for future expansion.
- **Multi-CLI support (Gemini, etc.)** — tracked in `docs/feature-backlog.md`. Current implementation is Claude-only.
- **Session persistence across restarts** — sidecar provides notification only; session state is not restored. This is a deliberate gap: collab sessions do not survive restarts the way single-agent sessions do. Mitigating this would require persisting the message queue and transcript to disk, which conflicts with the in-memory design principle.
- **Explicit `/end-session` command** — sessions end when both agents call `ask_human` signalling completion, or on timeout.

---

## 9. Files Touched

| File | Change |
|---|---|
| `server/collab.py` | New — `CollabSession` dataclass, session registry helpers |
| `server/registry.py` | Add `_sessions` dict; session create/lookup/delete methods |
| `server/gateway.py` | New `message_and_await_agent` tool handler; collab-aware `ask_human` routing; inject_queue polling |
| `server/spawn.py` | Parse `--agents=2` and `--relay` flags; collab spawn path; write `collab-sessions.json` sidecar |
| `scripts/spawn-launcher.ps1` | Handle multi-agent `spawn-pending.json`; open two terminal tabs |
| `server/firebase.py` | Write session messages/meta/inject_queue nodes; session-scoped ask_human |
| `android/.../MainViewModel.kt` | Sessions listener; speaker attribution; inject vs reply routing |
| `android/.../MainActivity.kt` | Session tab rendering; ask_human reply UI (banner, quote preview, compose modes) |
| `skill/SKILL.md` | Document `message_and_await_agent`; collab session protocol for agents |
