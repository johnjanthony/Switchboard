# Spawn Antigravity CLI via Switchboard

This design specification addresses adding support to Switchboard for spawning Antigravity (`agy`) CLI agents, in addition to the existing Claude Code agents. 

Based on investigation of the current system, we have the necessary hooks for both platforms, and `agy` supports the flags required to bind to a Switchboard session ID.

## Background & Technical Feasibility

From checking `agy --help` and the current `server/spawn.py` / `scripts/spawn-launcher.ps1`:
- `agy` supports forcing a conversation ID using `--conversation <uuid>`. We can use this just like Claude's `--session-id` flag to pre-bind the spawned agent.
- `agy` supports `--dangerously-skip-permissions` (to auto-approve tools without prompting).
- `agy` supports `-i` or `--prompt-interactive` to start an interactive loop with an initial prompt.

The command translation looks like this:
**Fresh spawn (Claude):** `claude 'prompt' --session-id 'uuid' --dangerously-skip-permissions`
**Fresh spawn (Antigravity):** `agy -i 'prompt' --conversation 'uuid' --dangerously-skip-permissions`

## Open Questions
> [!IMPORTANT]
> - Do you want the ability to choose between Claude and Antigravity **per-spawn** on your phone (UI approach)? Or do you just want to flip your **default** CLI to Antigravity universally without modifying the Android app right now (Config approach)?
> - In `agy`, what is the intended command flag for a "fresh" interactive spawn vs a "resume"? `agy -i <prompt>` works for fresh, but is it correct to omit `-i` for a pure resume: `agy --conversation <id>`?

---

## Competing Approaches

### Approach 1: UI-Driven Agent Selection (Recommended)
This approach adds an explicit selector to the Android spawn dialog so you can choose which CLI to spawn on the fly.

**Pros:** Total flexibility; transparent to use; supports mixed environments.
**Cons:** Requires an Android app rebuild and deployment.

**Implementation Details:**
1. **Android App (`SpawnSessionDialog.kt`)**: Add a new radio button group for "Agent" (`Claude` vs `Antigravity`).
2. **Android App (`MainActivity.kt` / Firebase)**: Inject `"agent": "claude" | "antigravity"` into the `spawn_commands` JSON sent to Firebase.
3. **Server (`server/spawn.py`)**: Forward the `agent` property into the JSON `spawn-pending-<uuid>.json` files consumed by the launcher script.
4. **Launcher (`scripts/spawn-launcher.ps1`)**: Read `$agent.agent` (defaulting to `"claude"` for backwards compatibility). Construct the launch command:
   - If `"antigravity"`: Use `agy -i '$psSafePrompt' --conversation '$sessionId' --dangerously-skip-permissions`.
   - If `"claude"`: Keep the existing `claude '$psSafePrompt' --session-id ...` command.
5. **WSL Support (`spawn-claude-wsl.sh`)**: We'll either update the script to take the CLI as an argument or create a `spawn-agy-wsl.sh` equivalent.

### Approach 2: Server-Config Default CLI
This approach introduces a `.env` configuration flag to change the default CLI that Switchboard launches. No changes are made to the Android app.

**Pros:** No Android build required; fast implementation.
**Cons:** Cannot mix Claude and Antigravity spawns dynamically; the phone UI will still say "Spawn Claude Session".

**Implementation Details:**
1. **Server Config (`server/config.py`)**: Add `default_cli = os.getenv("SWITCHBOARD_DEFAULT_CLI", "claude")`.
2. **Server (`server/spawn.py`)**: Automatically inject the configured `default_cli` into the `spawn-pending` payload.
3. **Launcher (`scripts/spawn-launcher.ps1`)**: Read the CLI preference from the pending file and launch `agy` or `claude` accordingly.

---

## Proposed Changes (Assuming Approach 1)

### Android
#### [MODIFY] android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SpawnSessionDialog.kt
- Add `agent` state: `"claude"` vs `"antigravity"`.
- Render Agent radio buttons (below Surface selection).
- Pass `agent` to `onSpawn` callback.

#### [MODIFY] android/app/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt
- Update `onSpawn` signature and attach `agent: String` to the Firebase `spawn_commands` payload.

### Server
#### [MODIFY] server/spawn.py
- Extract `agent = cmd.get("agent", "claude")` in `handle_fresh` and `handle_resume`.
- Insert `agent` into the `agents` dict written to `spawn-pending-*.json`.
- Remove the `_is_antigravity_session` check that currently blocks Antigravity resumes with "auto-resume is not supported yet".

### Scripts
#### [MODIFY] scripts/spawn-launcher.ps1
- Extract `$agentCli = if ($agent.PSObject.Properties.Name -contains 'agent') { $agent.agent } else { "claude" }`.
- Switch on `$agentCli` to compose the correct `$cli` string for both Windows and WSL spawns.

#### [NEW] scripts/spawn-agy-wsl.sh (if Approach 1 is selected)
- Create a WSL counterpart for `agy` (mirroring `spawn-claude-wsl.sh`), which accepts the prompt file and invokes `agy`.
