# Switchboard vs AgentOrchestrator (Forge) — Detailed Comparison

*Generated 2026-04-21*

---

## 1. Core Purpose

| | Switchboard | AgentOrchestrator (Forge) |
|---|---|---|
| **One-liner** | Human-in-the-loop communication bridge | Multi-agent debate orchestrator |
| **Core problem** | How does an agent get human input when the developer is away? | How do you get better AI output through adversarial collaboration? |
| **Primary actor** | Human developer (receives questions, sends answers) | Two AI agents (Claude + Gemini debating each other) |
| **Human role** | Decision-maker consulted mid-task | Optional supervisor / approver at consensus/stagnation points |

---

## 2. Architecture at a Glance

**Switchboard**
```
Claude Code Agent(s)
        │  MCP SSE (localhost:9876)
        ▼
  Switchboard Server (Python asyncio)
  ├── Registry (asyncio.Future per pending question)
  ├── Gateway (ask_human / notify_human / send_document_human)
  └── TelegramBackend (httpx polling + message_id correlation)
        │
        ▼
   Telegram API ──► Developer's Phone
```

**AgentOrchestrator (Forge)**
```
  orchestrator.json / CLI args
        │
        ▼
  MultiAgentOrchestrator (Java / Spring)
  ├── SessionLifecycleManager
  │   └── WorktreeService (git worktree per session)
  ├── SessionRunner (debate loop)
  │   ├── PromptGenerator
  │   ├── ClaudeCLI  ──subprocess──► claude CLI
  │   ├── GeminiCLI  ──subprocess──► gemini CLI
  │   └── TerminationService (consensus/deadlock/stagnation detection)
  ├── UserInputService
  │   ├── TerminalInputClient (JLine)
  │   └── TelegramBotService (telegrambots SDK)
  ├── GitHubService (PR creation on consensus)
  ├── ConversationLogger (Markdown + JSONL)
  └── EvolutionController (self-improvement state machine)
```

---

## 3. Technology Stack

| Dimension | Switchboard | AgentOrchestrator |
|---|---|---|
| **Language** | Python 3.11+ | Java 17+ |
| **Async model** | asyncio (fully async, no threads) | Spring + threads (JLine + SafePrintStream for thread safety) |
| **HTTP** | httpx (async) | Apache HttpClient via telegrambots SDK |
| **MCP** | FastMCP / mcp[cli] ≥1.2 | N/A — not an MCP server |
| **Web server** | uvicorn (SSE) | N/A |
| **Telegram** | Custom polling (httpx + getUpdates, correlation via message_id) | telegrambots SDK 6.8.0 (DefaultBotSession long-polling) |
| **Build** | pyproject.toml + pip | Maven 3.6+ with maven-shade-plugin (fat JAR) |
| **DI / Config** | python-dotenv + env vars | Spring Framework 5.3.27 + orchestrator.json |
| **Testing** | pytest + pytest-asyncio + respx | JUnit 5.10.0 + Mockito 5.5.0 |
| **Terminal UI** | N/A | JLine 3.26.3 + ANSI colors + status line + syntax highlighting |
| **Persistence** | In-memory only | Git worktrees + log files + optional Firebase |
| **Deployment** | Windows service via NSSM (auto-start on boot) | Manual java -jar (no auto-start) |

---

## 4. Agent Model — Fundamental Difference

**Switchboard is a transport layer.** It has no opinion about what agents do. Any agent that calls `ask_human` or `notify_human` via MCP gets the capability. The agent runs its own logic; Switchboard only handles the human communication channel.

**AgentOrchestrator is an orchestration layer.** It spawns agents as child processes, constructs their prompts, sequences their turns, interprets their output for termination markers, and controls the entire lifecycle. Agents are subordinate to the orchestrator.

```
Switchboard view of the world:
  Agent → [does its own work] → hits decision point → calls ask_human → human answers → continues

Forge view of the world:
  Orchestrator → spawns Agent A → feeds it prompt → reads output → 
                 spawns Agent B → feeds it Agent A's output → reads output →
                 detects [CONSENSUS_ACHIEVED] → commits worktree → creates PR
```

---

## 5. Human Interaction Comparison

| Interaction type | Switchboard | AgentOrchestrator |
|---|---|---|
| **Ask a blocking question** | ✅ Core feature (`ask_human`) | ✅ Via TelegramBotService (secondary) |
| **Fire-and-forget notification** | ✅ (`notify_human`) | ✅ Sends on consensus/stagnation events |
| **Send a file** | ✅ (`send_document_human`, with security validation) | ✅ Via Telegram file attachments |
| **Inline button suggestions** | ✅ Telegram inline keyboard | ❌ Not implemented |
| **Away mode protocol** | ✅ Formal protocol — agent routes all output through Switchboard | ❌ No formal protocol |
| **Remote session spawn** | ✅ `/spawn ProjectName [prompt]` → opens Windows Terminal tab | ❌ No spawn feature |
| **Terminal input** | ❌ Not applicable (server receives from agent) | ✅ JLine readline with history |
| **Inject mid-debate guidance** | N/A | ✅ User types text before next agent turn |
| **Workflow commands** | N/A | ✅ AUTO / MANUAL / RESET / RETRY / CONTINUE / STOP |

---

## 6. Telegram Integration — Side-by-Side

| Aspect | Switchboard | AgentOrchestrator |
|---|---|---|
| **Role of Telegram** | **Primary interface** — Telegram IS the product | **One of two input channels** alongside terminal |
| **Implementation** | Custom: httpx async polling (`getUpdates`), correlation via reply_to message_id | telegrambots SDK `DefaultBotSession` (long-polling) |
| **Correlation mechanism** | Telegram message_id (questions map to their reply thread) | None — messages processed in order |
| **Multi-agent routing** | ✅ `agent_id` parameter routes messages to correct agent | ❌ Single session only |
| **Blocking await** | ✅ asyncio.Future per question, unblocked when reply arrives | ❌ Notifications only — no blocking await |
| **Timeout handling** | ✅ 24h configurable timeout → returns `"__TIMEOUT__"` sentinel | ✅ 24h `telegramResponseTimeout` in config |
| **Config** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in .env | `telegramBotToken` + `telegramChatId` in orchestrator.json |
| **Can be disabled** | No (core feature) | ✅ `--no-telegram` flag or `telegramEnabled: false` |

---

## 7. Session and State Management

| Aspect | Switchboard | AgentOrchestrator |
|---|---|---|
| **Session concept** | Per-request (PendingRequest with asyncio.Future) | Per-debate (SessionState, session ID, worktree) |
| **Persistence** | In-memory only — restart loses all pending questions | Git worktrees + log files (Markdown + JSONL) — survives restart |
| **State tracking** | Registry: request_id ↔ correlation ↔ Future | SessionState: agent responses per turn, AtomicInteger counters |
| **Isolation** | None needed (no filesystem changes) | Git worktree per session — full isolation of code changes |
| **Logging** | JSONL audit trail (request_created, resolved, timeout, errors) | Markdown conversation log + JSONL + optional Firebase |
| **Session ID** | N/A | Captured from agent CLI output; used to resume agent sessions |
| **Cleanup** | Automatic — Future discarded after resolve/timeout | WorktreeService.cleanup() on consensus; preserved on deadlock/clarification |

---

## 8. Scope and Complexity

| Metric | Switchboard | AgentOrchestrator |
|---|---|---|
| **Language / LOC** | Python ~2,000 LOC | Java ~8,576 LOC across 95+ files |
| **Core packages** | 8 modules (registry, gateway, telegram, spawn, config, logging, messenger, main) | 18 packages (cli, git, evolution, logic, ui, terminal, notification, logging, input, models, utils...) |
| **Test files** | 16 test modules, 100+ tests | JUnit 5 + Mockito (quantity not documented) |
| **Docs** | README, CLAUDE.md, CLAUDE-JOURNAL.md, design spec, spawn spec, feature backlog | README, control flow reference (12KB), self-evolution design (28KB), Telegram config, Windows setup, feature roadmap, visual diagrams |
| **Responsibility** | Single: human ↔ agent communication | Multiple: orchestration + UI + git + GitHub + Telegram + Firebase + evolution |
| **Design philosophy** | Single-responsibility; minimal footprint; no agent logic | Rich orchestration; manages full debate lifecycle |

---

## 9. Isolation and Safety

| Safety feature | Switchboard | AgentOrchestrator |
|---|---|---|
| **Filesystem isolation** | No filesystem writes | Git worktree per session |
| **Path validation** | ✅ Relative paths only, deny-list (.env, *token*, *.pem, *.key), 5MB limit | ✅ Session ID sanitized (alphanumeric + hyphens only) |
| **Input sanitization** | N/A (server receives structured tool calls) | Null-byte removal, 5MB prompt size cap |
| **Agent permissions** | Agents have whatever permissions they already have | `--dangerously-skip-permissions` passed to Claude subprocess |
| **Telegram auth** | Responds only to configured TELEGRAM_CHAT_ID | Accepts only messages from authorized chat ID |
| **Reconfirmation rule** | N/A | If workspace modified while consensus declared, forces re-confirmation |

---

## 10. Self-Evolution Capability

| | Switchboard | AgentOrchestrator |
|---|---|---|
| **Self-improvement** | ❌ None by design | ✅ `EvolutionController` state machine |
| **Evolution states** | — | IDLE → PROPOSING → COMPILING → TESTING → VALIDATING → PROMOTING → COMPLETE |
| **What it does** | — | Debates propose changes to the orchestrator itself; compile + smoke-test + promote |
| **Abort mechanism** | — | Lock file checked between states |
| **Approval** | — | Optional `--require-approval` flag |

---

## 11. Deployment and Operational Model

| | Switchboard | AgentOrchestrator |
|---|---|---|
| **Startup** | Windows service (NSSM), auto-starts on boot | Manual `java -jar target/AgentOrchestrator-1.0-SNAPSHOT.jar` |
| **Lifetime** | Always-on daemon | Per-session process |
| **Multiple sessions** | ✅ Handles multiple concurrent agents via in-memory registry | ❌ One debate session at a time |
| **MCP integration** | ✅ Exposed as MCP server (SSE on localhost:9876) | ❌ Not an MCP server; stands alone |
| **Install complexity** | NSSM service install, .env config, one `claude mcp add` command | Maven build, orchestrator.json config |

---

## 12. What Each Does That the Other Cannot

**Switchboard only:**
- Acts as an MCP server — plugs into Claude Code natively without subprocess orchestration
- Handles multiple concurrent agents simultaneously (each with its own agent_id)
- Implements proper blocking await with correlation (asyncio.Future + message_id)
- Remote session spawn via `/spawn` command from Telegram
- Inline button suggestions for guided responses
- Formal "away mode" protocol for agents

**AgentOrchestrator only:**
- Runs two models in adversarial debate (Claude vs Gemini)
- Manages agent prompts, sequencing, and output parsing
- Git worktree isolation — code changes are sandboxed
- Automatic GitHub PR creation on consensus
- Rich terminal UI (JLine readline, status line, syntax highlighting, color themes)
- Self-evolution mode (modifies and recompiles itself)
- Mock mode for UI testing without real CLIs
- `summarize` mode — generates executive summary of debates
- Firebase integration for distributed state

---

## 13. Relationship and Complementarity

These projects are **complementary, not competing**. They operate at different layers:

- **Switchboard** solves: *"How does a running agent contact the human when the human is away?"*
- **AgentOrchestrator** solves: *"How do you coordinate multiple agents to produce better outputs and implement changes?"*

**Potential integration:** AgentOrchestrator currently implements its own Telegram integration via the telegrambots SDK. It could instead register as a Switchboard consumer, replacing its `TelegramBotService` with calls to `ask_human`/`notify_human` MCP tools. This would:
- Remove the telegrambots dependency from Forge
- Let both projects share a single Telegram bot
- Give Forge the proper blocking-await behavior that Switchboard provides
- Give Switchboard a richer consumer (multi-agent debates, not just single agents)

The debate agents that AgentOrchestrator spawns could also call Switchboard's `ask_human` if they hit decision points mid-debate — creating a three-tier hierarchy: human → Switchboard → AgentOrchestrator → agents.

---

## 14. Summary Table

| Dimension | Switchboard | AgentOrchestrator (Forge) |
|---|---|---|
| **Language** | Python | Java |
| **Role** | MCP transport bridge | Agent orchestrator |
| **Agents** | 1+ (any MCP-enabled agent) | 2 (Claude + Gemini, spawned as subprocesses) |
| **Human channel** | Telegram (primary) | Telegram + terminal |
| **Telegram depth** | Blocking await, correlation, multi-agent routing | Notifications + remote commands |
| **State** | In-memory (ephemeral) | Git worktrees + log files |
| **Deployment** | Always-on Windows service | Per-session fat JAR |
| **Code size** | ~2K LOC (Python) | ~8.5K LOC (Java) |
| **Self-evolution** | No | Yes |
| **MCP server** | Yes | No |
| **PR creation** | No | Yes (on consensus) |
| **Complementarity** | Could provide human-comms layer for Forge | Could consume Switchboard instead of rolling its own Telegram |
