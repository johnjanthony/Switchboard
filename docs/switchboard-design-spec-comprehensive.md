# Switchboard — Comprehensive Design Specification

**Version:** 3.1 (Updated with Constraints)
**Date:** 2026-04-26
**Status:** ⚠️ PARTIALLY STALE — see freshness notice below.

> **Freshness notice (2026-04-30).** This doc was last refreshed 2026-04-26 and predates several substantial architectural changes that have shipped since. References to `channel_id` should be read as `cwd` plus `sender`; the `away_mode_active` flag is now per-channel + global; `logs/away-mode.json` no longer exists; `gateway.py` is now the `server/gateway/` package; HTTP transport is stateful. For accurate detail, read the dated specs in [`docs/superpowers/specs/`](superpowers/specs/) and the [`PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md) entries from 2026-04-24 onward — when this doc disagrees with the dated specs, the dated specs win.
>
> Notable shipped-since-2026-04-26 milestones:
>
> - **cwd-as-channel routing** ([2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design](superpowers/specs/2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md)). Tools take `cwd` + `sender` instead of `channel_id`.
> - **Two-tier away mode + phone-built bulk-respond modal.** `global_settings/away_mode` + `channels/{key}/away_mode` overrides. `enter_away_mode(cwd)` / `exit_away_mode(cwd)` take a cwd argument.
> - **Firebase schema reorg** ([2026-04-28-away-mode-firebase-schema-reorg-design](superpowers/specs/2026-04-28-away-mode-firebase-schema-reorg-design.md)). Per-channel `away_mode`, `unread_count`, `pending_responses` live under `channels/{key}/`.
> - **Codebase-review HIGH sweep** ([2026-04-28-codebase-review](2026-04-28-codebase-review.md)). `gateway.py` split into `server/gateway/` package; bg-task tracking; collab deadlock guard + message coalescing; turn-end hook partner-blocked enforcement.
> - **Stateful HTTP transport (2026-04-30)**. `stateless_http=False` so per-tool-call cancel notifications propagate. Server startup auto-clears away mode globally so pre-restart agents don't get trapped in a Stop-hook loop.

---

## 1. Executive Summary

Switchboard is a robust, human-in-the-loop MCP gateway that allows AI agents (Claude Code, Gemini CLI) to request human judgment and deliver status updates while the developer is away from their desk. It bridges the local CLI environment to the developer's mobile device via a native Android app and Firebase Realtime Database.

The system emphasizes **reliability** (turn-end hooks to prevent terminal leaks), **context-rich collaboration** (multi-agent sessions), and **operational simplicity** (Windows service integration).

---

## 2. Problem Statement

AI agents are capable of high-autonomy tasks, but certain "dangerous" or high-impact actions (file overwrites, cloud deployments, complex refactors) require human approval. When the developer steps away, the agent either stalls, guesses incorrectly, or outputs to a terminal no one is watching.

Switchboard provides:
1. A **blocking transport** for approval requests.
2. A **notification channel** for status and documents.
3. An **orchestration layer** for multi-agent collaboration.
4. An **enforcement mechanism** to ensure the protocol is followed.

---

## 3. Core Architecture

Switchboard follows a "Bridge" pattern, connecting local agents to a remote human surface.

```text
Local Environment (Windows)                │ Cloud / Mobile
                                           │
[Agent 1 (Claude)] ───┐                    │
[Agent 2 (Gemini)] ───┤                    │
                      ▼                    │
              ┌────────────────┐           │     ┌───────────────┐
              │  Switchboard   │           │     │  Firebase     │
              │  MCP Server    │ ◄─────────┼────►│  Realtime DB  │
              │  (Python)      │           │     └───────┬───────┘
              └───────┬────────┘           │             │
                      │                    │             ▼
                      ▼                    │     ┌───────────────┐
              ┌────────────────┐           │     │  Android App  │
              │ Windows Task   │           │     │  (Kotlin)     │
              │ Scheduler / wt │           │     └───────────────┘
              └────────────────┘           │
```

### 3.1 Components
- **MCP Server:** A Python 3.11+ process (FastMCP) serving a streamable HTTP transport.
- **Firebase Realtime Database (RTDB):** The "source of truth" for message synchronization and state mirroring.
- **Firebase Cloud Messaging (FCM):** Delivers high-priority push notifications to the phone.
- **Android App:** Native Kotlin/Compose app for reading, replying, and spawning sessions.
- **NSSM Service:** Ensures Switchboard runs as a persistent background process.
- **Turn-End Hooks:** Python scripts triggered by the CLI to block turns when away-mode is active.

---

## 4. Communication Model: Unified Channel Routing

Switchboard uses **Unified Channel Routing** to organize multiple conversations into distinct "Channels."

### 4.1 Routing Keys
- **`channel_id`**: The unique identifier for a conversation (tab). Format: `{project_key}-{YYYYMMDD}-{HHmmss}` (UTC).
- **`sender`**: The display label for the agent (e.g., "Claude", "Gemini", "IR2").

### 4.2 Message Types
- **`question`**: Synchronous request from `ask_human`. Triggers heads-up notification and reply UI.
- **`notify`**: Asynchronous update from `notify_human`.
- **`agent`**: Peer-to-peer message in collab sessions.
- **`document`**: File delivery metadata.
- **`human`**: Response from the developer (appears on the right).

---

## 5. MCP Tools API

| Tool | Params | Blocking | Purpose |
|---|---|---|---|
| `ask_human` | `question`, `channel_id`, `sender`, `format`, `suggestions` | **Yes** | Seek approval or input. |
| `notify_human` | `message`, `channel_id`, `sender`, `format` | No | Status updates. |
| `send_document_human` | `path`, `channel_id`, `sender`, `caption` | No | File delivery (max 5MB). |
| `message_and_await_agent` | `channel_id`, `sender`, `message` | **Yes** | Peer-to-peer collab. |
| `enter_away_mode` | (none) | No | Flip global away-mode flag. |
| `exit_away_mode` | (none) | No | Clear global away-mode flag. |

### 5.1 formatting
- `format="plain"`: Raw text.
- `format="markdown"`: Renders with bold, italic, inline code (cyan), and code blocks on Android.

---

## 6. Human-in-the-Loop Surface (Android App)

### 6.1 Features
- **Tabbed Interface:** Each `channel_id` gets a dedicated tab.
- **Floating Action Button (FAB):** Opens the **Spawn Dialog** to launch new sessions.
- **Away-Mode Toggle:** A long-press-and-confirm pill chip in the TopAppBar to toggle the server's away-mode state.
- **Channel Hiding:** Non-destructive "hide" icon to move quiet channels into an overflow menu.
- **Bulk Respond:** On exiting away-mode, a dialog offers to send a shared response (e.g., "I'm back") to all pending questions.

### 6.2 Notifications
- **Questions:** `IMPORTANCE_HIGH` (banner + sound).
- **Documents:** `IMPORTANCE_DEFAULT`.
- **Updates:** `IMPORTANCE_DEFAULT`.

---

## 7. Away-Mode Enforcement

Enforcement ensures that agents cannot "leak" output to an unmonitored terminal.

### 7.1 State
- **Server Flag:** `away_mode_active` (bool) persisted in `logs/away-mode.json`.
- **Mirror:** Reflected in Firebase at `/away_mode/active` for the Android app.

### 7.2 Turn-End Hook
- **Trigger:** Claude Code `Stop` hook and Gemini CLI `AfterAgent` hook.
- **Logic:** Calls `GET http://localhost:9876/away-mode`. If `true`, the hook returns a **BLOCK** (Claude) or **DENY** (Gemini) decision, forcing the agent to continue and route via `ask_human`.
- **At-Desk Redirect:** If an agent calls `ask_human` while away-mode is **off**, the server returns an error: `"ERROR: John is at his desk. Ask this question via the terminal."`, steering the agent back to local chat.

---

## 8. Spawning & Collaboration

### 8.1 Spawning
Sessions are launched via Windows Terminal (`wt.exe`) inside a **Windows Scheduled Task** (`SwitchboardSpawn`). This bypasses "Session 0" isolation, allowing the service (running in background) to open interactive windows for the user.

### 8.2 Collaboration (`message_and_await_agent`)
- **Interaction Model:** Strictly **turn-based**. Agent A sends a message and blocks; Agent B (or a human) receives it, processes, and sends back.
- **Human Injection:** The developer can "inject" messages into the collab stream via the Android compose box.
- **Orchestration:** Spawning is currently a **human-triggered action**. Agents cannot natively spawn sub-agents mid-turn via Switchboard tools.

---

## 9. Security & Safety

- **Localhost Bound:** The MCP server binds to `127.0.0.1:9876`.
- **Credential Protection:** The Firebase Bot Token and Service Account are the primary auth boundaries.
- **Path Validation:** `send_document_human` rejects `.env`, `*.pem`, `*token*`, etc., and prevents `..` traversal.
- **Rate Limiting:** `notify_human` and `send_document_human` are limited (default 30/min) per channel to prevent runaway FCM usage.

---

## 10. Operational Guidelines

### 10.1 Service Management
Managed via NSSM. Scripts in `scripts/`:
- `install-service.ps1`: Installs as a user-logon service (required for spawn).
- `restart-service.ps1`: Restarts with a `pytest` gate.

### 10.2 Registry & Logging
- **Audit Log:** `logs/switchboard.jsonl` records all tool calls, resolutions, and spawns.
- **Session Logs:** `logs/sessions/{channel_id}.log` contains a human-readable transcript of channel traffic.

---

## 11. Configuration Summary

| Env Var | Default | Description |
|---|---|---|
| `SWITCHBOARD_PORT` | `9876` | Local port. |
| `SWITCHBOARD_TIMEOUT_SECONDS` | `86400` | Tool wait limit (24h). |
| `SWITCHBOARD_SPAWN_ROOT` | (none) | Root for `/spawn` command. |
| `SWITCHBOARD_RATE_LIMIT` | `30` | Tokens per minute. |
| `FIREBASE_DATABASE_URL` | (none) | Required for sync. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | (none) | Path to creds. |

---

## 12. Constraints & Architectural Decisions

### 12.1 Context & Isolation
Switchboard treates each `channel_id` as an **isolated communication silo**. There is no shared "Long-Term Memory" or "Memory Bank" across different remote sessions. An agent spawned in one repository has no access to the history or architectural decisions of an agent in another repository via Switchboard.

### 12.2 Execution Environment & Sandboxing
Agents are **not sandboxed** by Switchboard. When running in away mode (often with `--dangerously-skip-permissions` or `--yolo`), agents have full access to the local machine's filesystem and shell. **Safety is governed by instructions (`SKILL.md`)**, where agents are told to gate significant actions behind `ask_human()`. Switchboard enforces the *protocol* (no terminal leaks) but not the *execution* (autonomous tool use).

### 12.3 Input Modalities
The current system is optimized for **text-heavy interaction**.
- **Outbound:** Agents can deliver files/logs to the human via `send_document_human()`.
- **Inbound:** The human "injection" path from the Android app is **text-only**. Switchboard does not currently support multimodal input (photos, screenshots) being injected back into a session.

### 12.4 Interaction Model & Latency
The system follows a **Request/Response ("Ping-Pong") model**.
- It is not designed for streaming or duplex experiences.
- `ask_human()` is a synchronous blocking call. The agent remains idle until the Firebase `responses/` node is populated or the timeout is reached.
