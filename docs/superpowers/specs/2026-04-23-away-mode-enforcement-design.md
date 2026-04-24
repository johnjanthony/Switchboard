# Away-Mode Enforcement Design

**Date:** 2026-04-23
**Status:** Approved

## Problem

Agents forget they are in away mode after running for a while. When they forget, they emit chat text to the terminal instead of routing through `ask_human` / `notify_human` — and because John is not watching the terminal while away, those messages disappear into the void. The existing `SKILL.md` away-mode rules are load-bearing text inside the agent's context; they decay as the context window fills with tool results.

The gateway already knows that agents should not end turns outside a switchboard tool call when away. What it does not do is enforce that rule. This spec adds enforcement via a Claude Code `Stop` hook that blocks the agent from ending its turn while an away-mode flag is set server-side.

Related but distinct from the "Silence Detection at the Gateway" backlog item — that item proposed gateway-side transcript inspection; this spec uses the supported Claude Code hook surface instead.

## Approach

Two moving parts:

1. **Switchboard tracks away-mode state** as a global, persistent flag. Agents toggle it explicitly via two new MCP tools (`enter_away_mode` / `exit_away_mode`). Spawned sessions auto-enter on spawn. State persists across service restarts via a sidecar JSON file.
2. **A user-global turn-end hook** (Claude Code `Stop` + Gemini CLI `AfterAgent`) queries switchboard over HTTP on every agent stop event. If away mode is active, the hook emits a block/deny decision with a redirect reason to force the agent to continue. If switchboard is unreachable or the flag is off, the hook exits silently — no effect on non-switchboard sessions. Both CLIs are covered because Switchboard already supports spawning either backend (see "Multi-CLI agent orchestration" in the backlog).

The global-flag scope is V1. A per-channel upgrade is on the backlog.

## Server state (persistent)

Add `away_mode_active: bool` and `away_mode_entered_at: datetime | None` to `Registry`. Both are backed by a sidecar file at `logs/away-mode.json`:

```json
{"active": true, "entered_at": "2026-04-23T14:30:00Z"}
```

- **Load on startup:** `Registry.__init__` reads the file if present. Missing file or malformed JSON → default `active: false` silently. The flag is low-stakes (John can always re-enter via the tool) and a noisy startup log for a corrupt sidecar is not worth the alert burden.
- **Write on every toggle:** `enter_away_mode`, `exit_away_mode`, and the spawn-time auto-enter all commit the flag to disk before returning success to the caller. The agent never sees `"ok"` for a toggle that did not land on disk. The file is a single small JSON object, so the write is done inline with blocking stdlib I/O — consistent with the rest of `Registry`, whose methods are synchronous.
- **No auto-expiry in V1.** If the flag gets stuck (service killed mid-session, agent crashes before calling `exit_away_mode`), John calls `exit_away_mode` once. False-positive blocks are cheaper than false-negative silence leaks.

The `entered_at` field is for debugging and audit only in V1 — the hook and tools do not read it.

## MCP tools

Two new tools in `gateway.py` and `main.py`:

```python
async def enter_away_mode() -> str
async def exit_away_mode() -> str
```

Both are idempotent. Both return `"ok"` on success or `"ERROR: ..."` on failure. Neither takes a `channel_id` — the flag is global. Both write a JSONL audit event (`away_mode_entered` / `away_mode_exited`).

Existing tools (`ask_human`, `notify_human`, `send_document_human`, `message_and_await_agent`) are unchanged.

## Auto-enter on spawn

In `spawn.py`'s spawn handler, `registry.set_away_mode(True)` is called only **after the schtasks launch succeeds** — specifically, right after `self._last_spawn_time = ...` on the success path of both `_handle_single_spawn` and `_handle_collab_spawn`. A failed launch does not flip the flag, so the agent cannot leave the flag stuck by crashing before its first tool call. Spawned agents are inherently in away mode — this removes the "did the agent remember to call `enter_away_mode`?" race.

BYO collab sessions and already-running agents still call `enter_away_mode()` explicitly when John says "stepping away". The spawn auto-enter does not fire for BYO sessions (BYO does not go through `SpawnHandler`).

## HTTP query endpoint

Add one new route on the existing uvicorn app, alongside `/healthz`:

```
GET /away-mode → {"active": true|false}
```

Unauthenticated (localhost-bound, read-only). Returns `{"active": false}` on any internal error. Intentionally separate from `/healthz` so the hook has a tight, stable contract that does not drift when `/healthz` gains fields.

## Turn-end hook (Claude `Stop` + Gemini `AfterAgent`)

One shared Python script drives both CLIs. Python is used over shell because JSON parsing, HTTP timeouts, and exit-code discipline are cleaner, and both CLIs deliver their event payload as JSON on stdin.

**Script:** `scripts/turn-end-hook-away-mode.py`.

**Shared redirect reason string** (used by both CLIs):

> You are in away mode. John is not watching the terminal. End this turn by calling `ask_human()` to check in, or `notify_human()` to report status followed by `ask_human()`. Do not produce terminal output. If John has returned, call `exit_away_mode()` first.

**Shared behavior:**

1. Read the event payload from stdin. Ignore contents (payload shapes differ between Claude and Gemini; this V1 hook does not use any payload field — the per-channel backlog upgrade is what adds payload inspection).
2. `GET http://localhost:9876/away-mode` with `timeout=0.5s`.
3. Branch on the `--cli` argument passed by the hook registration (`claude` or `gemini`) and emit the correct JSON shape on `active: true` — see below.
4. On `active: false`, HTTP error, connection refused, timeout, or any other failure: exit 0 with no stdout. The hook is fail-open — it never blocks a session that is not connected to switchboard.

**Claude Code (`--cli claude`)** — registered in `~/.claude/settings.json` under `hooks.Stop`. On `active: true`, emit to stdout:

```json
{"decision": "block", "reason": "<shared redirect reason>"}
```

**Gemini CLI (`--cli gemini`)** — registered in `~/.gemini/settings.json` under `hooks.AfterAgent`. On `active: true`, emit to stdout:

```json
{"decision": "deny", "reason": "<shared redirect reason>", "continue": true}
```

Differences from Claude: the decision verb is `"deny"` (per Gemini's reference docs) and `"continue": true` is set explicitly to force a retry turn rather than ending the session. The injected `reason` becomes the next-turn prompt.

**Install scope:** user-global for both CLIs. The hook fires in every Claude Code / Gemini CLI session on the machine. The localhost HTTP overhead (~1ms) is negligible; the fail-open design means non-switchboard sessions are unaffected.

**Install script:** `scripts/install-turn-end-hook.ps1` — one-shot setup that writes a hook entry into `~/.claude/settings.json` (event `Stop`) and `~/.gemini/settings.json` (event `AfterAgent`), referencing the Python script at its repo path (no copy — updates to the script take effect the next time the hook fires). Creates the target settings files if absent. Writes a `.pre-install-<timestamp>.bak` beside each settings file before every modification. Idempotent — strips any prior entry whose command contains `turn-end-hook-away-mode` before appending. A `-Claude` / `-Gemini` flag pair lets the installer be scoped to one CLI if the other is not installed.

Runs on Windows PowerShell 5.1+ or PowerShell 7+. The Gemini hook command is prefixed with the PowerShell call operator (`& "python.exe" "script.py" --cli gemini`) because Gemini CLI's hook runner parses the command through `Invoke-Expression`, where a bare quoted path is a string literal and produces "Unexpected token" errors without the `&`. Claude Code's runner uses a different shell path that treats the first quoted token as the executable, so its entry does not need the prefix.

## Registry changes (`server/registry.py`)

```python
class Registry:
    def __init__(self, away_mode_path: Path | None = None) -> None:
        ...
        self._away_mode_path = away_mode_path
        self._away_mode_active, self._away_mode_entered_at = self._load_away_mode()

    def is_away_mode_active(self) -> bool: ...
    def set_away_mode(self, active: bool) -> None: ...
    def _load_away_mode(self) -> tuple[bool, datetime | None]: ...
    def _persist_away_mode(self) -> None: ...
```

All methods are synchronous, matching the existing `Registry` conventions. `_persist_away_mode` writes the sidecar with blocking stdlib I/O — the file is a single small JSON object, so event-loop impact is negligible.

## Gateway changes (`server/gateway.py`)

`ToolHandlers` gains two new handlers:

```python
async def enter_away_mode() -> str:
    try:
        registry.set_away_mode(True)
        logger.away_mode_entered()
        return "ok"
    except Exception as exc:
        logger.tool_error(None, None, str(exc))
        return f"ERROR: {exc}"

async def exit_away_mode() -> str:
    try:
        registry.set_away_mode(False)
        logger.away_mode_exited()
        return "ok"
    except Exception as exc:
        logger.tool_error(None, None, str(exc))
        return f"ERROR: {exc}"
```

Added to `ToolHandlers` dataclass and `build_tool_handlers` return value.

## Spawn changes (`server/spawn.py`)

`SpawnHandler.handle` calls `self.registry.set_away_mode(True)` after validating the spawn request and before launching the agent process. A `logger.away_mode_entered(reason="spawn")` event distinguishes spawn-triggered entries from tool-triggered entries.

## Main wiring (`server/main.py`)

- `_build_fastmcp` registers the two new tools (thin wrappers delegating to `handlers.enter_away_mode` / `handlers.exit_away_mode`).
- `_run` registers a Starlette route for `GET /away-mode` alongside the existing `/healthz`.
- `Registry` is instantiated with `away_mode_path=Path(config.log_path).parent / "away-mode.json"`.

## Logging (`server/logging_jsonl.py`)

Two new event types: `away_mode_entered` (with optional `reason` field) and `away_mode_exited`. Matches the existing logger method pattern (`notify_sent`, `request_created`, etc.).

## Skill documentation (`skill/SKILL.md`)

Update the "Away Mode Protocol" section:

- On entry (when John says "stepping away"), call `enter_away_mode()` immediately after (or before) the status-confirming `notify_human` call.
- On exit (when John says "I'm back"), call `exit_away_mode()` as the first action before resuming terminal output.
- Note that spawned sessions auto-enter away mode — the agent does not need to call `enter_away_mode()` itself at spawn time.
- Note that if the agent sees an unexpected "block + reason" (Claude) or "deny + reason" (Gemini) message from a turn-end hook, it is because away mode is still active; the correct response is to call `ask_human()` (or `exit_away_mode()` if John has explicitly returned).

## Tests

| File | What it covers |
|------|----------------|
| `tests/test_registry.py` | Add: `set_away_mode` persists; restart round-trip (write flag → new Registry → flag restored); corrupt-file handling returns default; missing-file returns default |
| `tests/test_gateway_away_mode.py` (new) | `enter_away_mode` / `exit_away_mode` flip Registry state, return `"ok"`, are idempotent, write audit events |
| `tests/test_main_routes.py` (extend if exists, else new) | `GET /away-mode` returns current flag; returns `false` when Registry throws |
| `tests/test_spawn_away_mode.py` (new, or add to existing spawn tests) | `SpawnHandler.handle` sets `away_mode_active=True` before launching |
| `tests/test_turn_end_hook.py` (new) | Script-level tests: mock HTTP response; assert Claude variant (`--cli claude`) emits `{"decision": "block", "reason": ...}` on `active: true`; assert Gemini variant (`--cli gemini`) emits `{"decision": "deny", "reason": ..., "continue": true}` on `active: true`; both variants silent-exit on `active: false`, connection error, timeout, non-200 |

Total new tests: ~15.

## Out of scope

- **Per-channel away-mode state.** V1 is global. A per-channel upgrade (with cwd-based hook correlation) is on the backlog.
- **Auto-exit on idle / staleness detection.** V1 requires explicit `exit_away_mode()` calls.
- **Gateway-side transcript inspection** (the other half of the "Silence Detection" backlog item). The Stop hook replaces the need for it.
- **Hook on other events** (PreToolUse, PostToolUse, and their Gemini analogues). V1 is turn-end-only. Other hooks can be added if turn-end proves insufficient.
- **Authenticated `/away-mode` endpoint.** Localhost-only, read-only, no sensitive data.
- **Metrics / telemetry on how often the hook blocks.** JSONL audit events cover this; no dashboard in V1.
