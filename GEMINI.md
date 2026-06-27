# Switchboard — Gemini CLI Orientation

This file contains Gemini-specific instructions for working in the Switchboard workspace.

## Mandates

- **FOUNDATIONAL MANDATE:** You MUST read and adhere to the project-wide orientation and conventions in [`CLAUDE.md`](CLAUDE.md).
- **FOUNDATIONAL MANDATE:** You MUST read and adhere to the tool usage and away mode protocol in [`skills/switchboard/SKILL.md`](skills/switchboard/SKILL.md).

## CRITICAL: Switchboard MCP tools currently do not work for Gemini

Under the v2 routing model, Switchboard routes every MCP call by a hook-injected `cli_session_id`. That injection is performed by Claude Code's `PreToolUse` hook (`scripts/cli-session-injector-hook.py`), which has **no Gemini CLI equivalent** (Gemini's hook system has no PreToolUse-style event that can rewrite a tool call's input).

As a result, **Gemini agents currently cannot use ANY Switchboard MCP tool.** Every call is rejected at the MCP boundary by the `require_cli_session_id` guard (`server/gateway/handlers.py`) with:

> ERROR: cli_session_id required. This call appears to come from a Claude session without the switchboard plugin's PreToolUse hook installed, or from a non-Claude agent. Switchboard tools require hook-injected session_id under the v2 routing model.

This is a known, accepted limitation, not a bug to debug (recorded in `docs/superpowers/specs/2026-05-19-conversations-collab-redesign-design.md`). Gemini regains Switchboard MCP access only once an equivalent injection capability exists on the Gemini side.

**What still works:** the away-mode `AfterAgent` hook (see Setup below). It fires after every agent turn (analogous to Claude Code's `Stop` hook) and queries the server over plain HTTP (`GET /away-mode`), not through the MCP boundary, so the `cli_session_id` gate does not affect it.

**What does not work:** all MCP tools (`ask_human`, `notify_human`, `send_document_human`, the conversation tools, `set_away_mode`) return the rejection above, and the real-time agent-status indicator on the phone does not update for Gemini sessions (it depends on the same missing hook). Do not attempt to call `/agent_status` yourself.

## Setup

To wire your Gemini CLI session to the local Switchboard gateway:

```bash
gemini mcp add switchboard http://localhost:9876/mcp --type http --trust
gemini skills link ./skills/switchboard
```

**Note:** registering the MCP connection succeeds, but under the current v2 routing model every Switchboard MCP tool call from Gemini is rejected (see the section above); only the `AfterAgent` away-mode hook below is functional today.

The `AfterAgent` turn-end hook is installed separately. Run:

```powershell
scripts/install-turn-end-hook.ps1
```
