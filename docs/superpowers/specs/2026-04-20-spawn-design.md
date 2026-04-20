# Switchboard Spawn Feature — Design Specification

**Project:** Switchboard — Telegram-triggered Claude Code spawn
**Status:** Design approved; ready for implementation planning
**Version:** 2.0
**Date:** 2026-04-20

---

## 1. Overview

Switchboard gains the ability to spawn a new Claude Code session from a Telegram command. The developer sends `/spawn [project-path] [prompt]` from their phone; Switchboard verifies the message originates from the configured `TELEGRAM_CHAT_ID`, launches the session in a new Windows Terminal tab, and acknowledges. The session runs visibly in the taskbar — the developer attaches by clicking the tab on return.

The agent communicates during the session via `ask_human`/`notify_human` as normal. No in-process session monitoring or `/sessions` tracking: the visible Windows Terminal tab is the monitor.

This completes the away-mode story: never-stop-asking keeps existing sessions alive; spawn starts new work streams remotely with a clean attach-on-return path.

---

## 2. Command: `/spawn`

```text
/spawn [project-path] [prompt]
```

Both arguments are optional. Parsing rule applied left-to-right:

1. If the text after `/spawn` is empty → project=`SWITCHBOARD_SPAWN_ROOT`, prompt=absent.
2. Otherwise, try to resolve the first token as a path under `SWITCHBOARD_SPAWN_ROOT`. If `spawn_root / first_token` exists and is a directory → first token is the project-path, remainder is the prompt.
3. If the first token does not resolve to an existing directory → entire text is the prompt, project defaults to `SWITCHBOARD_SPAWN_ROOT`.

This gives four valid forms:

```text
/spawn                                  → project=root,               prompt=absent
/spawn rpdm/next-gen                    → project=root/rpdm/next-gen, prompt=absent
/spawn fix the migration                → project=root,               prompt="fix the migration"
/spawn rpdm/next-gen fix the migration  → project=root/rpdm/next-gen, prompt="fix the migration"
```

When prompt is absent, Switchboard uses a default: `"You've been spawned in <project-key>. Use ask_human to ask the developer what they'd like you to work on, with agent_id='<project-key>'."` The agent's first action is then to ask for a task via Telegram.

Authorization is implicit: the command is only accepted if `message.chat.id == TELEGRAM_CHAT_ID`. The chat ID is a permanent numeric identifier tied to the developer's Telegram account; commands from any other chat ID are silently ignored.

**Success reply (with prompt):**

```text
Spawning rpdm/next-gen with task 'Fix the IR2 migration step tha...'. Check Windows Terminal.
```

**Success reply (no prompt):**

```text
Spawning rpdm/next-gen — agent will ask what to work on. Check Windows Terminal.
```

---

## 3. Architecture

```text
Telegram getUpdates loop
  ├─ reply_to_message  →  IncomingResponse  →  dispatch_responses()  (unchanged)
  └─ /spawn            →  asyncio.Queue[str]  →  poll_commands()  →  dispatch_commands()  →  SpawnHandler
```

One new module (`server/spawn.py`). Targeted changes to `server/telegram.py`, `server/gateway.py`, `server/config.py`, and `server/main.py`.

---

## 4. Components

### 4.1 `server/spawn.py`

**`SpawnHandler`** — stateful only for rate limiting:

- `last_spawn_time: datetime | None`
- `handle(raw: str) -> None`
- `_handle_spawn(text: str) -> None`
  1. Check rate limit (global 1 spawn / 60s); if exceeded send `"Rate limited. Try again in <n>s."` and return
  2. Parse `text` using the two-argument resolution rule from §2: try first token as a directory under `spawn_root`; if it resolves, remainder is prompt; if not, entire text is prompt and project is `spawn_root`
  3. Verify resolved project path starts with `spawn_root` (blocks `..` traversal) and is an existing directory; on failure send `"Unknown project: <key>."`, log `spawn_invalid_path`, return
  4. If prompt is empty, substitute default: `"You've been spawned in <project-key>. Use ask_human to ask the developer what they'd like you to work on, with agent_id='<project-key>'."`
  5. Launch via Windows Terminal: `subprocess.Popen(["wt", "new-tab", "--", "claude", "-p", effective_prompt, "--dangerously-skip-permissions"], cwd=str(project_path))`
  6. Write JSONL audit entry: `spawn_started` with spawn_id (8-char random hex), project_key, project_path, prompt_preview (`"(ask on start)"` if default)
  7. Send ack via `backend.send_spawn_ack(project_key, prompt_preview)`

`subprocess.Popen` is used (not `asyncio.create_subprocess_exec`) because `wt` is a launcher that exits immediately after opening the tab — there is no process to await or monitor. The actual `claude` session runs inside Windows Terminal independently.

### 4.2 `server/telegram.py` changes

`__init__` gains `self._command_queue: asyncio.Queue[str] = asyncio.Queue()`.

The existing `getUpdates` loop gains a branch after the existing `reply_to_message` check:

```python
elif text and text.startswith("/spawn"):
    if str(msg.get("chat", {}).get("id")) == self._chat_id:
        await self._command_queue.put(text)
    # silently ignore commands from unexpected chat IDs
```

Reply correlation is unchanged. The `chat.id` check is the sole authorization gate.

New public method:

```python
async def poll_commands(self) -> AsyncIterator[str]:
    while True:
        yield await self._command_queue.get()
```

One new send method:

- `send_spawn_ack(project_key: str, prompt_preview: str | None) -> None` — `None` prompt_preview produces the "agent will ask what to work on" variant

### 4.3 `server/gateway.py` changes

New coroutine alongside `dispatch_responses`:

```python
async def dispatch_commands(
    spawn_handler: SpawnHandler,
    backend: TelegramBackend,
    logger: JsonlLogger,
) -> None:
    async for raw in backend.poll_commands():
        try:
            await spawn_handler.handle(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.surface_error(f"dispatch_commands_error: {exc}")
```

### 4.4 `server/config.py` changes

One new optional field on `Config`:

```python
spawn_root: Path | None          # None = spawn disabled
```

`load_config()` reads `SWITCHBOARD_SPAWN_ROOT`. If absent, `spawn_root` is `None` and spawn is disabled gracefully — `/spawn` commands receive `"Spawn not configured."`.

### 4.5 `server/main.py` changes

```python
spawn_handler = SpawnHandler(config, backend, logger)
spawn_task = asyncio.create_task(
    dispatch_commands(spawn_handler, backend, logger)
)
```

`spawn_task` is cancelled and awaited in the existing `finally` block alongside `dispatch_task`.

---

## 5. Security

| Threat | Mitigation |
| --- | --- |
| Unauthorized spawn command | `message.chat.id` verified against `TELEGRAM_CHAT_ID` before queuing; silently ignored if mismatched |
| Bot token leak → spawn | Attacker with bot token cannot forge `chat.id` on incoming messages — only the real Telegram account can originate commands |
| Path traversal (`../Windows`) | Resolve full path; verify it starts with `spawn_root` |
| Arbitrary project access | Only paths under `SWITCHBOARD_SPAWN_ROOT` accepted |
| Spawn flood | Global rate limit: 1 spawn per 60 seconds |

---

## 6. Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `SWITCHBOARD_SPAWN_ROOT` | No | Root directory. Spawn paths resolved relative to this. Spawn disabled if absent. |

Example `.env` addition:

```text
SWITCHBOARD_SPAWN_ROOT=C:\Work
```

Authorization uses the existing `TELEGRAM_CHAT_ID` — no separate spawn token required.

**Prerequisite:** Windows Terminal (`wt`) must be on `PATH`. It is installed by default on Windows 11.

---

## 7. Error Responses

| Condition | Telegram reply |
| --- | --- |
| Spawn not configured | `"Spawn not configured."` |
| First token resolves to non-existent directory | `"Unknown project: <key>."` |
| Path outside spawn root | `"Unknown project: <key>."` |
| Rate limited | `"Rate limited. Try again in <n>s."` |
| Subprocess launch failure | `"Failed to spawn: <sanitized error>."` |

Invalid path attempts are logged as `spawn_invalid_path` in the JSONL audit log.

---

## 8. JSONL Audit Log Events

| Event | Fields |
| --- | --- |
| `spawn_started` | spawn_id, project_key, project_path, prompt_preview |
| `spawn_invalid_path` | project_key, resolved_path |

---

## 9. Testing

- **`tests/test_spawn_handler.py`** — all four argument-parsing forms, path traversal rejection, rate limiting, `subprocess.Popen` mocked (verify correct `wt` invocation and cwd), default-prompt substitution, ack sent with correct args
- **`tests/test_telegram_commands.py`** — `poll_commands` yields `/spawn` messages; wrong `chat.id` silently dropped; reply-to messages still go to `poll_responses`; bare `/spawn` (no args) is correctly enqueued
- **`tests/test_gateway_dispatch_commands.py`** — `dispatch_commands` routes to handler; exceptions logged without killing the loop

No test launches a real `wt` process or `claude` subprocess. `subprocess.Popen` is mocked.

---

## 10. Out of Scope

- `/sessions` command — visible Windows Terminal tab is the session monitor; not needed
- In-process session monitoring / exit notifications — same rationale
- `/kill <key>` — no session registry to key off
- Per-project rate limits — global 1/60s sufficient for single-developer use
- BotFather `/setcommands` registration — cosmetic; doesn't affect functionality
- Output capture from spawned sessions — agents report via `ask_human`/`notify_human`
