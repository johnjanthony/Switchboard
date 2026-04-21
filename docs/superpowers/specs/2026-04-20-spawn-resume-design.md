# Switchboard — Spawn-and-Resume Design Specification

> **ABANDONED (2026-04-21).** No mechanism exists in Claude Code to trigger proactive `ask_human` at session start without a user turn. `-p`, positional args, `additionalContext` hooks, and transcript injection all fail. Decision: do not restart Switchboard while in away mode. This spec is retained for historical context only.

**Project:** Switchboard — Human-in-the-Loop MCP Gateway for Claude Code
**Status:** Design approved; ready for implementation planning
**Date:** 2026-04-20

---

## 1. Problem

Restarting the Switchboard service severs all active SSE connections. Claude Code's MCP client does not auto-reconnect. Any agent session that was relying on Switchboard tools (`ask_human`, `notify_human`) will see MCP tool errors on the next call, complete its current turn, and go idle.

This makes service restarts incompatible with away mode — deploying a bug fix or config change while away means losing the active agent session.

---

## 2. Goal

Allow an agent running in away mode to trigger a Switchboard service restart, then automatically resume the prior agent session in a new terminal tab once the service is back up — without losing the conversation context or requiring the developer to intervene.

---

## 3. Design Constraints

- The agent cannot resume its own session (it exits when the MCP connection drops permanently after service restart).
- `claude --continue` (`-c`) resumes the most recent session for the current project directory. In headless (`-p`) mode, when the MCP connection drops, the tool call returns an error string to the model; the model completes its turn; the process exits cleanly. The session is then the "most recent" and `-c` picks it up correctly.
- If `claude -c` is run against a session that is still running, it creates a fork from that point rather than hijacking the running session — this is non-destructive.
- The spawn-launcher infrastructure already exists (Task Scheduler task + PowerShell launcher + pending JSON handoff). The resume path is additive to that existing mechanism.
- No server-side Python changes are needed. This is entirely a scripts-and-guidance change.

---

## 4. Data Flow

```
Agent calls restart-service.ps1 -Spawn
  → Script: nssm stop switchboard
  → MCP SSE connection drops; any in-flight ask_human returns an error string to the model
  → Model receives tool error, completes its turn, exits (headless mode)
  → Session is now "most recent" for that project directory
  → Script: pytest gate runs
  → Script: nssm start switchboard
  → Script: writes logs/spawn-pending.json with {"resume": true, "project_path": "<cwd>"}
  → Script: schtasks /run SwitchboardSpawn
  → spawn-launcher.ps1 runs (in user's desktop session via Interactive logon)
  → Launcher reads spawn-pending.json, sees resume:true
  → Launcher: Start-Process wt ... "claude -c --dangerously-skip-permissions"
  → New terminal tab opens, claude -c picks up the most recent session
  → Agent resumes with full conversation context; Switchboard tools available again
  → Agent immediately calls ask_human to confirm restart succeeded; message content reflects what it knows (agreed next step, a proposal, or an open question)
```

---

## 5. Changes

### 5.1 `scripts/restart-service.ps1`

Add a `-Spawn` switch (default off). When `-Spawn` is set, after the service successfully restarts:

1. Write `logs/spawn-pending.json`:
   ```json
   {"resume": true, "project_path": "C:\\Work\\Switchboard"}
   ```
   `project_path` must be the path where the agent session lives — use `$AppDir` (already defined in the script as `C:\Work\Switchboard`).

2. Run `schtasks /run /tn SwitchboardSpawn` to trigger the launcher.

3. Print a message: "Spawning resume session..." so the output confirms what happened.

When `-Spawn` is **not** set, behavior is identical to today — no spawn-pending.json written, no schtasks call.

The existing WARNING about the stale MCP connection remains, but only shows when `-Spawn` is **not** set (if `-Spawn` is set, the resume takes care of reconnection automatically).

### 5.2 `scripts/spawn-launcher.ps1`

Add a resume branch: if `$params.resume -eq $true`, launch:

```powershell
Start-Process -FilePath "wt" `
  -ArgumentList "new-tab", "--", "claude", "-c", "--dangerously-skip-permissions" `
  -WorkingDirectory $params.project_path
```

The existing prompt path (when `resume` is absent or false) is unchanged.

Full branching logic:

```powershell
if ($params.resume -eq $true) {
    Start-Process -FilePath "wt" `
      -ArgumentList "new-tab", "--", "claude", "-c", "--dangerously-skip-permissions" `
      -WorkingDirectory $params.project_path
} else {
    Start-Process -FilePath "wt" `
      -ArgumentList "new-tab", "--", "claude", "-p", $params.prompt, "--dangerously-skip-permissions" `
      -WorkingDirectory $params.project_path
}
```

### 5.3 `skill/SKILL.md`

Add a "Restarting Switchboard" section with the guidance that when instructed to restart Switchboard (to apply a code change, config change, or any update), the agent must use `.\scripts\restart-service.ps1 -Spawn` rather than the bare `restart-service.ps1`. This ensures the session resumes automatically after the service comes back up.

Example section content:

> ## Restarting Switchboard
>
> When instructed to restart the Switchboard service, always use:
>
> ```powershell
> .\scripts\restart-service.ps1 -Spawn
> ```
>
> The `-Spawn` flag writes a resume marker before triggering the Windows Terminal launcher. After the service restarts, a new terminal tab opens and `claude -c` resumes this session automatically.
>
> Never run `restart-service.ps1` without `-Spawn` while in away mode — the MCP connection will drop and the session cannot recover.
>
> **After resuming**, the first action must be an `ask_human` call confirming the restart succeeded. The content should reflect what the agent actually knows from the resumed conversation context:
>
> - If a next step was already agreed before the restart, confirm it: "Switchboard restarted. About to proceed with X — confirm?"
> - If the agent has a clear proposal based on context, offer it: "Switchboard restarted. I'd suggest tackling Y next — sound right?"
> - If context is unclear, ask openly: "Switchboard restarted successfully. What's next?"
>
> Do not silently proceed with prior work. The developer needs to confirm the resume worked and re-establish intent before the agent continues.

### 5.4 `CLAUDE.md`

Add the same guidance to the project CLAUDE.md (which is read by agents working on Switchboard itself, not just consumers of it). It belongs in the "Service management" section alongside the existing NSSM commands.

---

## 6. What Does NOT Change

- `server/spawn.py` — no Python changes; `spawn_pending.json` format gains a `resume` key but `spawn.py` never reads the file, only writes the normal spawn format.
- `server/gateway.py`, `server/telegram.py` — no changes.
- The Telegram `/spawn` command — this continues to write `{"prompt": "...", "project_path": "..."}` (no `resume` key). The launcher's else-branch handles this correctly.
- The SwitchboardSpawn scheduled task registration — no change to install-service.ps1.
- Rate limiting in SpawnHandler — not applicable; this flow bypasses SpawnHandler entirely.

---

## 7. Failure Modes

| Failure | Effect | Mitigation |
|---------|--------|------------|
| pytest gate fails | Script exits before writing spawn-pending.json; no spawn; service stays stopped | Developer receives exit-code failure in terminal or Telegram notification before script ran |
| schtasks call fails | No launcher triggered; service is running but no resume | Log the error; existing send_text mechanism surfaced the problem |
| claude -c picks up wrong session | Resumes a different project's session | This happens only if another project was the most recent; avoided by project_path being Switchboard's directory |
| New terminal tab fails (wt not found) | Same failure mode as normal spawn; logged | Existing spawn-launcher failure handling applies |

---

## 8. Files Changed

| File | Change |
|------|--------|
| `scripts/restart-service.ps1` | Add `-Spawn` switch; conditional spawn-pending write + schtasks call |
| `scripts/spawn-launcher.ps1` | Add `if ($params.resume)` branch launching `claude -c` |
| `skill/SKILL.md` | Add "Restarting Switchboard" section |
| `CLAUDE.md` | Add restart guidance to "Service management" section |

---

## 9. Testing

Manual smoke test (no automated test needed — this involves Task Scheduler, Windows Terminal, and external process launch):

1. Start an away-mode agent session in a terminal (`claude --dangerously-skip-permissions`).
2. Make the agent call `notify_human` to confirm Switchboard is working.
3. In a second terminal, run `.\scripts\restart-service.ps1 -Spawn` (elevated PowerShell).
4. Confirm: tests pass, service restarts, a new Windows Terminal tab opens, `claude -c` resumes the session.
5. In the resumed session, confirm `notify_human` works (MCP reconnected successfully).
6. Confirm `spawn-pending.json` was consumed and does not remain on disk.
