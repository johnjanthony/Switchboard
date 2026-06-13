# Switchboard — Claude Code Orientation

This file contains Claude-specific instructions for working in the Switchboard workspace.

## Mandates

- **Read [`AGENTS.md`](AGENTS.md)** for project shape, layout, conventions, and service management.
- **Read [`skills/switchboard/SKILL.md`](skills/switchboard/SKILL.md)** for MCP tool signatures and the Away Mode protocol.
- **If [`docs/project_next_session.md`](docs/project_next_session.md) exists**, read it first — it contains branch-specific resumption notes (committed with whichever feature branch you're on) describing in-progress work, remaining steps, and any pre-merge cleanup items.

## MCP tool surface

Active tools: `ask_human`, `notify_human`, `send_document_human`, `message_and_await_agent`, `open_conversation`, `enter_conversation`, `combine_conversations`, `lookup_conversation_ids`, `leave_conversation`, `set_away_mode`.

Routing is by `cli_session_id`, injected by the `cli-session-injector-hook.py` PreToolUse hook. Agents pass `sender` and tool-specific args only.

## Conversation model

Conversations are the persistence + routing unit. States: `Active` / `Ended`. At most one Active conversation is the "open" singleton (set by `open_conversation()`; joinable via `enter_conversation()`). Routing key is `cli_session_id` (hook-injected), not cwd. Away mode is a single global flag (`set_away_mode(bool)`).

## Hooks (plugin bundle)

- **`cli-session-injector-hook.py`** (PreToolUse) — injects `cli_session_id` and `cwd` into every switchboard MCP call. Agent never passes these.
- **`cli-session-end-hook.py`** (SessionEnd) -- fires on orderly exit; writes a SessionEnd marker file under `SWITCHBOARD_MARKER_DIR` (the reliable path that wins the process-exit race), which the server sweeps to mark the member dormant (not auto-leave). The legacy `POST /cli-session/end` route remains for manual/testing use only.

## Setup

Switchboard ships as a Claude Code plugin. From any Claude Code session:

```
/plugin marketplace add C:/Work/switchboard
/plugin install switchboard@switchboard
```

The plugin install wires the skill and the Claude turn-end + agent-status hooks. Three things are installed separately:

1. **The MCP server connection.** A parallel chezmoi dotfiles effort bootstraps the user-scope MCP entry per host (Windows uses `localhost`; WSL uses the Windows host IP, which is per-machine). If you are not using chezmoi, run:

	```bash
	# Windows
	claude mcp add switchboard --scope user --transport http http://localhost:9876/mcp

	# WSL (replace <windows-host-ip> with the value from `/etc/resolv.conf` or `ip route show default | awk '{print $3}'`)
	claude mcp add switchboard --scope user --transport http http://<windows-host-ip>:9876/mcp
	```

	WSL must be running in bridge networking mode (NOT mirrored). The Windows server requires `SWITCHBOARD_HOST=0.0.0.0` and a firewall inbound rule for TCP 9876 from the WSL subnet.

	For WSL agents, also point the hook scripts at the Windows host so their HTTP callbacks don't fall back to `127.0.0.1` (unreachable from WSL). Set these in the WSL environment (e.g., in `~/.bashrc`):

	- `SWITCHBOARD_BASE_URL=http://<windows-host-ip>:9876` -- read by both HTTP hooks (`agent-status-hook.py` POSTs to `/agent_status`; `turn-end-hook-away-mode.py` GETs `/away-mode`).
	- `SWITCHBOARD_MARKER_DIR=<path>` -- read by `cli-session-end-hook.py`, which writes a SessionEnd marker FILE (not an HTTP POST) that the server sweeps; point it at the server's `<logs>/session-end` dir when the hook runs on a different host.

	The two HTTP hooks are now unified under `SWITCHBOARD_BASE_URL` (the old `SWITCHBOARD_AGENT_STATUS_URL` / `SWITCHBOARD_URL` vars are retired); `cli-session-end-hook.py` is marker-file based and reads `SWITCHBOARD_MARKER_DIR` instead of any HTTP URL.

2. **The Python server (NSSM Windows service).** Install with `scripts/install-service.ps1`. The plugin's MCP connection is useless until this is running.

3. **The Gemini CLI `AfterAgent` hook** (only if you use Gemini). Install with `scripts/install-turn-end-hook.ps1`. Gemini's hook system is independent of the Claude plugin.