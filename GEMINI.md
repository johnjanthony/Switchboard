# Switchboard — Gemini CLI Orientation

This file contains Gemini-specific instructions for working in the Switchboard workspace.

## Mandates

- **FOUNDATIONAL MANDATE:** You MUST read and adhere to the project-wide orientation and conventions in [`AGENTS.md`](AGENTS.md).
- **FOUNDATIONAL MANDATE:** You MUST read and adhere to the tool usage and away mode protocol in [`skills/switchboard/SKILL.md`](skills/switchboard/SKILL.md).

## CRITICAL: PreToolUse hook constraint

Gemini CLI's `AfterAgent` hook fires after every agent turn (analogous to Claude Code's Stop hook). The away-mode check lives there.

**There is no PreToolUse equivalent in Gemini CLI.** Claude Code's `PreToolUse` agent-status hook (`scripts/agent-status-hook.py`) cannot be registered for Gemini. This means:

- Gemini agents do NOT send `WORKING` / `WAITING` status updates to Switchboard in real time.
- The Android "agent is working" / "agent is waiting" status indicator will not update for Gemini sessions.
- This is a known gap — not a bug to debug. If you're running as a Gemini agent, do not attempt to call `/agent_status` yourself; the server will handle the missing status gracefully.

## Setup

To wire your Gemini CLI session to the local Switchboard gateway:

```bash
gemini mcp add switchboard http://localhost:9876/mcp --type http --trust
gemini skills link ./skills/switchboard
```

The `AfterAgent` turn-end hook is installed separately. Run:

```powershell
scripts/install-turn-end-hook.ps1
```
