# Switchboard — Comprehensive Design Specification

Switchboard is a locally-hosted MCP gateway that lets AI agents (Claude Code, Gemini CLI) pause mid-task to reach the developer via a native Android app. Agents block on `ask_human`, fire `notify_human`, and deliver files via `send_document_human` while the developer is away from their desk; the developer answers from the phone and the agent unblocks. Multi-agent conversations are first-class — two or more agents collaborate through a unified `Conversation` primitive, regardless of which OS surface or working directory each one runs on.

This doc is the single design reference for the running system. Implementation files are the ultimate source of truth; the dated specs under `docs/superpowers/specs/` retain the historical reasoning and are no longer authoritative for current behavior.

---

## 1. Architecture

```text
Local host (Windows)                              │ Cloud / Mobile
                                                  │
[Claude Code (Win)] ──┐                           │
[Claude Code (WSL)] ──┤                           │
[Gemini CLI       ] ──┤                           │
                      ▼                           │
            ┌───────────────────────┐             │   ┌─────────────────┐
            │  Switchboard server   │ ◄───────────┼──►│  Firebase RTDB  │
            │  (Python, FastMCP,    │             │   └────────┬────────┘
            │   Starlette HTTP)     │             │            │
            └─────┬───────────┬─────┘             │            ▼
                  │           │                   │   ┌─────────────────┐
                  │           ▼                   │   │  Android app    │
                  │   ┌──────────────┐            │   │  + Wear app     │
                  │   │ NSSM service │            │   │  (Kotlin/Compose│
                  │   │ wrapper      │            │   │   + FCM)        │
                  │   └──────────────┘            │   └─────────────────┘
                  ▼
            ┌──────────────────────┐
            │ SwitchboardSpawn     │
            │ scheduled task →     │
            │ Windows Terminal     │
            │ → claude on Win/WSL  │
            └──────────────────────┘
```

**Components.**

- **MCP server** (Python 3.11+, FastMCP with `stateless_http=False`). Serves the streamable HTTP transport on `127.0.0.1:9876` by default (`SWITCHBOARD_HOST` defaults to `127.0.0.1`; set to `0.0.0.0` for WSL-reachable; `SWITCHBOARD_PORT` defaults to `9876`). Plus five HTTP endpoints (`/healthz`, `/away-mode`, `/stats`, `/agent_status`, `/cli-session/end`) for hook callbacks and health checks.
- **Firebase Realtime Database** — the persistence and phone-side synchronization surface. The server writes; the Android app reads + writes replies.
- **Firebase Cloud Messaging (FCM)** — delivers push notifications. Three channels (Asks / Updates / Documents) drive separate notification priorities.
- **Android app** (`android/app/`) and Wear OS app (`android/wear/`) — the human surface. Kotlin/Compose. Notifications, conversation list, conversation view, reply input, spawn dialog, resume dialog, combine dialog, global away pill.
- **Plugin-bundled hooks** — four Python hook scripts (`scripts/`) registered through the Claude Code plugin (`hooks/hooks.json`). They inject routing data, mark dormancy, gate turn-end, and write agent-status.
- **NSSM Windows service** — wraps the Python server as a persistent background process under the developer's user session (required for spawn to open interactive windows).
- **SwitchboardSpawn scheduled task** — launches `wt.exe` (Windows Terminal) tabs running `claude` on either the Windows surface (PowerShell) or WSL (`wsl -e bash -lc ...`). The task runs in the developer's interactive desktop session so it can open visible windows.

---

## 2. Conversation model

A `Conversation` is the persistence + routing unit. UUID4 identifier. Carries title, lifecycle state, member rosters, message log, pending question slots, wait queue, and display metadata. All in-memory state lives in `server/registry.py:Conversation`; persistent fields mirror to Firebase under `/conversations/<id>/`.

### 2.1 Conversation states

- **Active** — at least one member (alive or dormant). Accepts new members via `enter_conversation`, `combine_conversations`, spawn-into-existing, or resume.
- **Ended** — terminal. No active or dormant members. Persists in Firebase as history; not loaded into the in-memory registry on restart.

Transitions to Ended happen on exactly four paths:

1. The last alive member explicitly calls `leave_conversation` AND no dormant members remain.
2. A sole-alive member calls `message_and_await_agent` in a non-open-marker conversation (auto-leave; see §6).
3. John force-ends from the phone (Page A swipe-right or long-press → End).
4. The conversation is the `source_id` of a `combine_conversations` call.

A separate path — **lobby timeout** — force-ends an orphan bootstrap conversation when `open_conversation` on the mint path times out without a peer joining.

### 2.2 Member states

Each `ConversationMember` carries `cli_session_id`, `sender` (agent-supplied display name), `cwd` (informational), `surface` (`"windows"` | `"wsl"`), `joined_at`, `last_seen_seq`, plus three state flags:

- **Alive** — `alive=True`, `cli_session_id` bound in `_session_to_conversation_id`. The agent process is running.
- **Dormant** — `alive=False`, `session_lost_permanently=False`. The CLI session ended cleanly (`SessionEnd` hook fired). The member is retained in `members_active` for potential revival via resume or combine.
- **Permanently lost** — `alive=False`, `session_lost_permanently=True`. `SessionEnd` fired with reason `clear` or `compact` — Claude rewrote or reset the session, so the CLI session is unrecoverable. Member is retained for visibility only; resume and combine skip these members.

### 2.3 Session-fallback rule

When a session is **removed** from a conversation (leave, force-end, sole-alive auto-leave; combine moves rather than removes), the session is not orphaned:

- **If `global_away_mode == True`** — the session re-binds to its **home conversation**, set on first switchboard contact and persisted in `_session_home_conversation_id`. If the home is still Active, the session is re-added as an alive member. If the home is Ended, the server mints a fresh Active conversation and updates the session's home pointer.
- **If `global_away_mode == False`** — the session becomes unbound. Subsequent `ask_human` / `notify_human` calls fall through to the at-desk redirect (§7.2); the agent's output reaches the developer via the terminal.

Combine is exempted from session-fallback — it migrates members from source to target rather than removing them.

### 2.4 Open marker (singleton)

`registry.open_conversation_id: conversation_id | None` is a server-side global pointer to the (at most one) Active conversation that joiners self-join via `enter_conversation`. Mirrored to Firebase under `/global_settings/open_conversation_id`.

- Set or replaced by `open_conversation()`.
- Cleared when the referenced conversation transitions to Ended (any path).
- Cleared and the Firebase node deleted (`ref.delete()` — `ref.set(None)` is incompatible with `firebase_admin`) when the conversation auto-ends, force-ends, combine-source-ends, or lobby-times-out.
- Hydration on server restart validates the restored pointer against the in-memory conversation set; if it dangles (referenced conv missing or Ended), hydration clears it in memory and on the backend so the system self-heals.

---

## 3. Routing

The routing key is **`cli_session_id`** — a UUID assigned by Claude Code per session and read by the `cli-session-injector-hook.py` PreToolUse hook from the hook-event input JSON. The hook merges `cli_session_id` and `cwd` into every switchboard MCP tool call's `updatedInput`. The agent never knows or supplies its own session id; it only passes `sender` and tool-specific arguments.

Calls missing `cli_session_id` are rejected at the MCP boundary by the `@require_cli_session_id` decorator with:

```
ERROR: cli_session_id required. This call appears to come from a Claude session without the switchboard plugin's PreToolUse hook installed, or from a non-Claude agent. Switchboard tools require hook-injected session_id under the v2 routing model.
```

The agent-supplied `cwd` is **informational** — used for display (Android conversation row, member roster) and for surface inference (`windows` if it looks like a drive path, `wsl` otherwise) — never for routing decisions. Two agents at the same physical directory or at different directories collab cleanly regardless of how they spell their cwd.

`Registry` carries three routing maps:

- `_session_to_conversation_id: dict[cli_session_id, conversation_id]` — the primary routing lookup.
- `_session_home_conversation_id: dict[cli_session_id, conversation_id]` — the session-fallback target per §2.3.
- `_open_conversation_id: conversation_id | None` — the open marker per §2.4.

---

## 4. MCP tool surface

Ten tools, all decorated with `@require_cli_session_id`. Every tool accepts agent-supplied arguments plus hook-injected `cli_session_id` and `cwd`. Signatures and current behavior:

### `ask_human(question, sender, title?, format?, suggestions?)` — blocking

Sends a question to John's phone; blocks for `SWITCHBOARD_TIMEOUT_SECONDS` (default 86400 = 24h) or until John replies. If the caller's session isn't bound to a conversation, the server auto-mints one first and routes the question through it.

**At-desk redirect.** When `global_away_mode == False`, the call short-circuits: the question is written to Firebase as a `notify`-type message (so it still surfaces on the phone as a passive notification), and the tool returns the literal string `"ERROR: John is at his desk. Ask this question via the terminal."` The agent's expected response is to surface the question content verbatim in the terminal.

`format`: `"plain"` (default) or `"markdown"`. `suggestions`: optional list of quick-reply strings that render as tap-to-respond chips on the phone.

### `notify_human(message, sender, title?, format?)` — non-blocking

Writes a one-way notification. Auto-mints a conversation if the session is unbound. At-desk-redirected when away mode is off (same gating as `ask_human`, but the call always returns `"ok"` and never waits).

Return contract (R1, decided 2026-06-11): `"ok"` when global away mode is on. When away mode is off the notification is still written and pushed, but the call returns `"ERROR: John is at his desk (notification delivered to phone anyway)."` so the agent learns to route remaining output to the terminal. The sentinel is not a failure and there is nothing to re-send.

### `send_document_human(path, sender, title?, caption?)` — non-blocking

Delivers a file to the phone. `path` is relative to the caller's cwd or absolute. 5 MB cap. Denylist on `.env*`, `*token*`, `*secret*`, `*.pem`, `*.key`. Path-traversal rejected. **Not at-desk-gated** — documents always deliver regardless of away mode.

### `message_and_await_agent(sender, message, title?)` — blocking

Speak to peers in your conversation and block until woken. `message` is required and non-empty (the listen-without-speaking use case is `enter_conversation`).

- **Caller unbound** → `"ERROR: not in any conversation. End your turn."`
- **Caller has alive peers** → append the speak event to the conversation log; queue caller in `wait_queue`; wake the FIFO-oldest waiter; block. Returns the talking-stick payload (delta since `last_seen_seq` excluding own emissions) when woken.
- **Caller is sole-alive in a non-open-marker conversation** → auto-leave path: append speak; remove caller from `members_active`; append to `members_history`; transition the conv to Ended if no dormant members remain; clear open marker if it pointed here. Returns `"__CONVERSATION_EMPTY__"` (optionally followed by partings observed since `last_seen_seq`).
- **Caller is sole-alive in the open-marker conversation** → lobby-hold: append speak; block on `conv.open_peer_future` until either a peer joins (waking with `"ok. open_conversation = <id>\nPeer '<name>' joined."`) or timeout (returns `"__TIMEOUT__"`; conv stays alive; caller remains a member). The lobby never auto-leaves an open-marker conversation.

`title`, when supplied, updates `conv.title`.

### `open_conversation(sender, title?)`

**Two behaviors** depending on whether the caller is already in a conversation:

- **Promote (caller is bound)** — non-blocking. Flips `registry.open_conversation_id` to the caller's current conv. Updates conv title if `title` supplied. Renames the caller's member entry if `sender` differs from the existing key (disambiguating against collisions). Returns `"ok. open_conversation = <id>"`.
- **Mint (caller is unbound)** — blocking. Mints a fresh Active conversation, adds the caller as the sole alive member, sets the open marker, then blocks on `open_peer_future` until a peer joins. On wake returns `"ok. open_conversation = <id>\nPeer '<name>' joined."` On timeout force-ends the orphan conv and returns `"__TIMEOUT__"`. The wait IS the API — bootstrap is one atomic call.

### `enter_conversation(sender)` — blocking

Unified "join + listen for intro." Five branches:

1. **Bound + open is your conv, OR no open exists** → queue for intro in current; block.
2. **Unbound + open exists** → add as new member of open; queue for intro; block.
3. **Bound + open ≠ current** → migrate from current to open (`_migrate_member`); queue for intro; block. Source conversation ends if you were its sole alive member with no dormant peers.
4. **Unbound + no open** → `"ERROR: no open conversation. Ask John to open one on the phone, or have an agent already in a conversation call open_conversation."`
5. **Bound but you're not in `members_active` of your bound conv** → server-state inconsistency; error.

On wake, returns the talking-stick payload (full history if newly added; delta since `last_seen_seq` otherwise).

### `combine_conversations(source_id, target_id)` — non-blocking

Move every movable member of `source_id` into `target_id`; source ends.

- **Alive members** rewire `_session_to_conversation_id`; `_migrate_member` moves them in-place. The migrate path resolves the target's `open_peer_future` (if set) so any blocked opener wakes with the migrating peer's sender.
- **Dormant members** are queued for resume — the launcher fires `claude --resume <session_id>` per-member into a spawn-pending file with `type="combine_resume"`. On the resumed agent's first MCP call, they land in the target.
- **Permanently lost members** stay in source as history (since source ends, they remain in `members_history`).

If source had the open marker, it clears. Target's open status, if any, is unaffected.

### `lookup_conversation_ids(cwd_filter?, sender_contains?, title_contains?)` — non-blocking

Returns a JSON-encoded list of Active conversation IDs matching ALL provided filters. At least one filter required. Iterates `registry.conversations` (in-memory only — Ended convs are excluded by the `state == "active"` filter). Used by agents to resolve concrete conversation IDs before calling `combine_conversations`.

### `leave_conversation(sender, parting_message)` — non-blocking

Explicit leave. Appends a `parting`-type message to the conversation log; removes the caller from `members_active`; appends to `members_history` with `left_at = now()`. Wakes the FIFO-oldest waiter with the parting in their payload. Applies the session-fallback rule (§2.3). If the caller was the sole alive member and no dormant members remain, the conversation transitions to Ended.

No "cannot leave in away mode" guard — the session-fallback rule covers the orphan-prevention case.

### `set_away_mode(value)` — non-blocking

Sets `registry.global_away_mode` to `bool(value)` and mirrors to Firebase at `/global_settings/away_mode`. The agent calls this in response to "I'm stepping away" or "I'm back" signals from John. Spawn-from-phone auto-enables away mode on dispatch (§7.1), so spawned agents typically don't need to set it themselves.

---

## 5. HTTP endpoints

Five routes mounted on the Starlette app:

| Route | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Liveness + per-loop supervisor status. Used by external monitoring. |
| `/away-mode` | GET | Returns the current global away-mode flag. Consumed by `turn-end-hook-away-mode.py` to gate turn-end. Query param `cwd` is informational only (logged). |
| `/stats` | GET | Widget/Watchtower roll-up: active_conversations, pending_count, oldest_pending_age_seconds, away_mode, healthy. |
| `/agent_status` | POST | Hook-driven status writes. Body: `{session_id, state, detail}`. Server resolves the session to a conversation and writes per-sender entries to `/conversations/<id>/agent_status/<sender>` (gated on global away mode; `state == "clear"` deletes). |
| `/cli-session/end` | POST | SessionEnd-hook callback. Body: `{session_id, reason}` where reason ∈ {`logout`, `clear`, `compact`, `other`}. Server marks the matching member dormant; for `clear`/`compact` additionally sets `session_lost_permanently=True`; wakes blocked waiters with a dormancy system message; cancels any open `ask_human` futures owned by the departed member. |

A StaticFiles mount at `/dashboard` serves the Operator cockpit.

The MCP transport is mounted under `/mcp` (FastMCP, streamable HTTP, stateful).

---

## 6. Plugin hooks

Four Python hook scripts bundled with the Claude Code plugin. Registered via `hooks/hooks.json`:

| Hook | Event | Purpose |
|---|---|---|
| `cli-session-injector-hook.py` | PreToolUse | Self-filters on `tool_name.startswith("mcp__switchboard__")`. Reads `session_id` and `cwd` from the hook payload, merges them into the tool's input via `hookSpecificOutput.updatedInput`. The agent never passes either field directly. **`updatedInput` replaces the input** (despite docs claiming merge), so the hook explicitly carries forward every original `tool_input` field. **Stdin must be read as raw bytes** (`sys.stdin.buffer.read()` + `json.loads(bytes)`) — `json.load(sys.stdin)` uses a TextIOWrapper that on Windows defaults to cp1252+surrogateescape and mangles UTF-8 (em-dashes, emojis) into surrogate codepoints. |
| `cli-session-end-hook.py` | SessionEnd | Writes a SessionEnd marker FILE under `SWITCHBOARD_MARKER_DIR` (atomic temp + `os.replace`), which the server's `dispatch_session_end_markers` loop sweeps to mark the member dormant (T-146: the marker file wins the process-exit race a synchronous POST loses; see section 13.3). The legacy `POST /cli-session/end` route remains for manual/testing use. Fires on orderly Claude exit (`/exit`, Ctrl+D, terminal closed); does NOT fire on SIGKILL / BSOD / network loss, which leave the member stale-alive. |
| `agent-status-hook.py` | PreToolUse, PostToolUse, UserPromptSubmit, Stop | POSTs `{session_id, state, detail}` to `POST /agent_status` to surface "thinking" / "tool:X" indicators on the phone. Server gated on global away mode; writes are dropped at-desk. |
| `turn-end-hook-away-mode.py` | Stop | Calls `GET /away-mode`. If the response says away mode is on, returns a `BLOCK` decision (Claude) so the agent's turn cannot end until output has been routed through `ask_human` / `notify_human`. |

A separate `install-turn-end-hook.ps1` script installs the Gemini CLI variant of the turn-end hook (Gemini's hook system is independent of the Claude plugin).

---

## 7. Talking-stick FIFO

Each conversation carries a single `wait_queue: collections.deque` plus the `open_peer_future: asyncio.Future | None` slot.

**`wait_queue` entries** are plain dicts with keys `member`, `future`, `waiting_kind` (`"enter"` | `"msg_and_await"`), `block_position` (monotonic timestamp). FIFO-ordered.

**Stick holder** is the at-most-one currently non-blocked alive member.

**`_wake_one_from(conv)`** pops the FIFO-oldest entry, composes a wake payload via `_compose_wake_payload` based on the entry's `waiting_kind`, resolves the future, and updates `member.last_seen_seq` to the current message count.

**Wake payload semantics:**
- `enter` kind → full history if `last_seen_seq == 0` (newly added); delta since `last_seen_seq` otherwise.
- `msg_and_await` kind → delta since `last_seen_seq`, **excluding** entries where `sender == caller.sender`.

**`open_peer_future`** is the separate wake slot used by mint-path `open_conversation` and by lobby-hold `message_and_await_agent` (sole-alive in open-marker conv). Resolved by:
- `_add_member` when a new alive member is added to the conv (via `enter_conversation` join, branches 2-3).
- `_migrate_member` when a member migrates in (via Branch 3 of `enter_conversation`, or via `combine_conversations` alive-member move).
- `handle_force_end` when the conv is force-ended (resolves with `"__CONVERSATION_ENDED__\n(force-ended)"`).
- `handle_session_end` when the caller's session dies mid-block (resolves with the dormancy system message).

Wake payload format: `"ok. open_conversation = <id>\nPeer '<name>' joined."` On timeout, mint-path force-ends the conv first, then returns `"__TIMEOUT__"`; lobby-hold returns `"__TIMEOUT__"` and leaves the conv alive.

**Force-end** (`handle_force_end`) drains the queue with `"__CONVERSATION_ENDED__\n(force-ended)"`, clears members, marks the conv Ended, clears the open marker if matched, and applies session-fallback per member.

---

## 8. Away mode

Single global flag at `registry.global_away_mode: bool`, mirrored at `/global_settings/away_mode`. Mutated by:

- `set_away_mode(bool)` MCP tool.
- Phone-written `away_mode_commands/<push_id>/` records (consumed by `dispatch_away_mode_commands`).
- Spawn dispatch auto-enables when launching from the phone.

### 8.1 At-desk redirect

When the flag is `False`:

- `ask_human` writes the question to Firebase as a `notify`-type message and returns `"ERROR: John is at his desk. Ask this question via the terminal."` The agent's expected behavior is to surface the question content in the terminal.
- `notify_human` is similarly gated.
- `send_document_human` always delivers regardless.
- `message_and_await_agent`, `enter_conversation`, `open_conversation`, `leave_conversation`, `combine_conversations`, `lookup_conversation_ids` are agent↔agent and unaffected by away-mode state.

### 8.2 Turn-end gating

The Stop hook (`turn-end-hook-away-mode.py`) queries `/away-mode`. When away is on, the hook injects a `BLOCK` decision so the agent's turn cannot end until output has been routed through one of the human-facing tools. This prevents terminal leaks.

### 8.3 Bulk-respond on exit

When the developer flips away off via the phone while pending `ask_human` calls are open, the phone surfaces a bulk-respond modal: send a shared response (e.g., "I'm back") to all pending questions, or cancel them, or do nothing. Logic in `server/gateway/bulk_respond.py:_apply_bulk_respond_decision`.

---

## 9. Spawn

Spawn launches a fresh `claude` process on either the Windows or WSL surface via the `SwitchboardSpawn` scheduled task. Single-agent per spawn; multi-agent conversations compose via spawn-into-existing or via the open-conversation handshake.

### 9.1 Spawn types

- **`fresh`** — phone writes `spawn_commands/<id>/{type:"fresh", surface, project, prompt?, target_conversation_id?}`. Server (in `server/spawn.py:SpawnHandler.handle_fresh`) validates, mints (or resolves) the conversation, pre-binds the new `cli_session_id`, writes a spawn-pending JSON file for the launcher, and triggers `schtasks /run /tn SwitchboardSpawn`.
- **`resume`** — phone writes `spawn_commands/<id>/{type:"resume", source_conversation_id, prompt?}`. Server validates eligibility (every member must have a non-null `cli_session_id` and not be `session_lost_permanently`), mints a new conversation with `continued_from = source.id`, pre-binds each member's `cli_session_id` to the new conv, ends the source if it was Active-all-dormant, and writes a multi-agent spawn-pending file.
- **`combine_resume`** — server-internal type. Issued per dormant member as part of `combine_conversations`. The dormant member is auto-resumed into the target conv.

### 9.2 Surfaces

- **Windows**: `project_path = config.windows_spawn_root / project` (e.g., `C:\Work\Switchboard`). Launcher opens `wt new-tab -- powershell.exe -EncodedCommand <b64>` running `Set-Location <path>; claude '<prompt>' --session-id <uuid> --dangerously-skip-permissions` (or `--resume <uuid>` for resume).
- **WSL**: `project_path = <wsl_home_resolved>/<wsl_spawn_root_segment>/<project>` (e.g., `/home/john/work/Switchboard`). The launcher writes the prompt to a one-shot file (`logs/spawn-prompt-<uuid>.txt`, deleted after read) and opens `wt new-tab -- wsl.exe -e bash -l /mnt/c/Work/Switchboard/scripts/spawn-claude-wsl.sh '<path>' <session-flag> <session-id> <prompt-file>`; the static script reads the prompt, then runs `cd '<path>' && claude '<prompt>' <session-flag> '<uuid>' --dangerously-skip-permissions`. (The inline `bash -lc` form was abandoned because wt does not preserve outer double-quoting when forwarding long quoted args, so the prompt rides in a file.) The WSL home is resolved once at server startup via `wsl.exe -e bash -lc 'echo $HOME'` and cached as `registry.wsl_home_resolved`. If WSL is unavailable on the host, the cache stays `None` and WSL-targeted spawns reject with `"WSL spawn requested but WSL is not available on this host."`

The two surfaces use **independent working trees**. A "Switchboard" project on Windows lives at `C:\Work\Switchboard`; the WSL clone (if any) lives at `/home/john/work/Switchboard` — a separate filesystem, not the drvfs view of the Windows path.

### 9.3 Launcher script

`scripts/spawn-launcher.ps1` reads the spawn-pending JSON, iterates `$params.agents`, branches on `$agent.surface` to pick the launch shell, and branches on `$params.type` to pick the session flag (`--session-id` for fresh; `--resume` for resume / combine_resume). The atomic-rename claim pattern prevents double-launch when multiple `schtasks /run` invocations land.

### 9.4 Auto-away on spawn

Spawn dispatch sets `global_away_mode = True` if currently False, mirrors to Firebase, and the phone surfaces a confirmation toast. Rationale: spawn-from-phone strongly implies the developer is away; explicit toggling per spawn is friction.

### 9.5 Member-add-on-first-call

A spawned agent's `cli_session_id` is pre-bound in `_session_to_conversation_id` before launch. On the first switchboard MCP call, the agent's `_add_member` (via the hook-injected session_id resolving into the pre-bound conv) creates the member entry. The agent picks its own `sender` per the prompt template's guidance.

### 9.6 Session-file aging

Claude Code prunes session files (`~/.claude/projects/<dir-hash>/<session_id>.jsonl`) after `cleanupPeriodDays` (default **30 days**). A conversation whose youngest dormant member's `session_ended_at` is older than that becomes non-resumable. The Android Page A row shows a `⚠️` warning indicator when any member is within 5 days of the cleanup window (now - 30 days < session_ended_at < now - 25 days).

---

## 10. Firebase schema

```text
conversations/<conversation_id>/
  meta/
    title                       (str)
    state                       "active" | "ended"
    continued_from              (conversation_id | null)
    created_at                  (epoch seconds, float)
    last_activity_at            (epoch seconds, float)
    ended_at                    (epoch seconds, float | null)
    hidden                      (bool)
    preview                     (str — latest message snippet)
  unread_count                  (int)
  pending_responses             (int — badge count)

  members_active/<sender>/
    cli_session_id              (str — primary routing key)
    sender                      (str)
    cwd                         (str)
    surface                     "windows" | "wsl"
    alive                       (bool)
    session_lost_permanently    (bool)
    session_ended_at            (str | null)
    session_end_reason          (str | null)
    joined_at                   (float)
    last_seen_seq               (int)

  members_history/<sender>/     # explicit-leave + force-end + auto-leave departures
    (same fields as members_active, plus left_at)

  messages/<push_id>/           # Firebase push keys, lexicographically time-ordered
    seq                         (int -- only on server-internal dict-form messages such as force-end/combine/spawn notices; absent on question/notify/document/agent_msg/parting; the Android client does not read it)
    sender                      (str)
    type                        "agent_msg" | "question" | "human" | "notify"
                                | "document" | "parting" | "system"
    text                        (str)
    url                         (str | null)
    filename                    (str | null)
    request_id                  (str | null — links question to response)
    attached_to_msg_id          (str | null — phone-side in-line reply linkage)
    timestamp                   (iso-8601)
    format                      "plain" | "markdown"
    suggestions                 (list[str] | null)
    cancelled                   (bool)
    rejected                    (bool)
    title                       (str | null — snapshot)
    opened                      (bool)

  pending_questions/<request_id>/
    sender                        (str)
    questionText                  (str)
    msgId                         (str | null)
    suggestions                   (list[str] | null)
    cancelled                     (bool)

  answers/<request_id>/         # phone -> server: John's reply to a pending ask_human
    text
    sender
    request_id
    written_at

  agent_status/<sender>/        # per-member; written only while away mode is on
    state                       "thinking" | "tool:<name>" | ...
    detail                      (str | null)
    updated_at                  (Firebase server-timestamp sentinel)

cli_sessions/<session_id>/
  home_conversation_id          (conversation_id)

global_settings/
  away_mode                     (bool)
  open_conversation_id          (conversation_id | absent — deleted, not set null)
  wsl_available                 (bool)

away_mode_commands/<push_id>/   # phone -> server
  type                          "enter_global" | "exit_global"
  issued_at                     (iso-8601)
  decision                      "send_default" | "skip"   (exit_global only; "cancel" dismisses the modal client-side without writing a command)
  default_text                  (str)                                 (exit_global, send_default only)

force_end_commands/<push_id>/   # phone → server
  conversation_id
  issued_at

combine_commands/<push_id>/     # phone → server, or server-internal
  source_conversation_id
  target_conversation_id
  issued_at

spawn_commands/<push_id>/       # phone → server
  type                          "fresh" | "resume"
  surface                       "windows" | "wsl"   (fresh only)
  project                       (str)               (fresh only)
  prompt                        (str | null)
  target_conversation_id        (conversation_id | null)  (fresh: optional join-existing)
  source_conversation_id        (conversation_id)         (resume only)
  issued_at

admin_notifications/<push_id>/
  sender                        "system"
  type                          "notify"
  text
  format                        "markdown"
  timestamp
```

### 10.1 Clear-write convention

Setting a Firebase node to `None` via `ref.set(None)` raises `ValueError('Value must not be None.')` in `firebase_admin`. All Switchboard setters that accept a nullable value (e.g., `set_open_conversation_id`, `set_session_home`) route the `None` case through `ref.delete()`. The node is **absent** after a clear, not `null`-valued.

### 10.2 Message type reference

| Type | Producer | Notes |
|---|---|---|
| `agent_msg` | `message_and_await_agent` | Speak events between agents. |
| `question` | `ask_human` (away mode on) | Carries `request_id` and optional `suggestions`. |
| `human` | Phone reply (server dispatch) | John's reply, linked via `attached_to_msg_id` to the original question. |
| `notify` | `notify_human`, or at-desk-redirected `ask_human` | Phone-side passive notification. |
| `document` | `send_document_human` | Carries `url`, `filename`. |
| `parting` | `leave_conversation` | Phone renders as `[X left] <text>`. |
| `system` | Server-internal | Dormancy notices, combine markers, force-end notices, resume opening. |

---

## 11. Hydration and restart-survival

On startup, `server/hydration.py:hydrate_from_firebase` rebuilds the in-memory registry from Firebase:

1. **Global settings** — `away_mode`, `open_conversation_id`.
2. **Conversations** — every `conversations/<id>/` node whose `meta/state == "active"` is restored. Ended conversations are skipped (they live in Firebase as history but aren't loaded into memory).
3. **Open-pointer validation** — if `_open_conversation_id` is set but the referenced conv wasn't hydrated (Ended or missing), hydration clears the pointer in-memory AND calls `backend.set_open_conversation_id(None)` to delete the Firebase node. The system self-heals from dangling pointers without requiring manual intervention.
4. **Session home pointers** — `cli_sessions/<session_id>/home_conversation_id`, skipping any pointer whose home isn't in the hydrated set (avoids re-binding to Ended homes).
5. **Session-to-conversation bindings** — derived from each Active conversation's members: only ALIVE members are re-bound. Dormant members are deliberately left unbound (the steady-state invariant is "dormant = unbound"); resume re-binds and flips a member alive only when it actually relaunches. Re-binding dormant members at hydration previously broke phone Resume permanently after a restart (H03/M21).

**Not rehydrated** by design:
- `wait_queue` and `pending_responses` — futures die with the process. Any agent blocked at restart times out on its next MCP call or returns CancelledError.
- `Conversation.open_peer_future` — same reason.
- `Conversation.lock` — a fresh `asyncio.Lock` is recreated.
- `pending_questions/<request_id>/` subtree - read at startup by `sweep_orphaned_pending_questions` (P1), which cancels orphaned records whose futures died with the old process. (The `answered_question_msg_ids` subtree was retired in P5 / F-66-F-73: the phone derives answered-state from message flags, so the write had no reader.)

The "never restart during an active multi-member conversation" operational rule reflects the wait_queue loss; persistence of in-flight futures is future work.

---

## 12. Android UI

Two main screens plus three dialogs.

### 12.1 Page A — conversation list

Top app bar carries the global **away pill** (long-press to toggle in either direction; tapping it does nothing). Turning away off raises a bulk-respond modal only when pending `ask_human` questions exist; with none pending it turns off immediately. Body is a vertical list of conversation rows — Active by default, plus Ended rows so the history is visible (Ended convs render with greyed-out menu items).

Each row shows: title, latest-message preview, last-activity timestamp, unread badge, pending-response badge, agent-status indicator, optional "open" marker if `conv.id == open_conversation_id`, "stale session" `⚠️` if any member is within 5 days of the 30-day session-file cleanup window.

Swipe gestures (both raise a confirmation dialog before mutating):
- **Swipe right** → End conversation. The long-press "End conversation" menu item is hidden for Ended convs, but the swipe-right gesture still raises the End-confirm dialog (force-end is a no-op on an already-ended conversation). Writes a `force_end_commands/<id>/` record; server-side `handle_force_end` applies session-fallback.
- **Swipe left** → Hide. Toggles `meta.hidden`. Hidden rows live behind the overflow menu's "Show hidden ($n)" toggle.

Long-press menu items (gated by conversation state):
- **Resume** — shown when at least one member is dormant-and-resumable. Greyed (with tooltip) when no member meets criteria. Opens the resume dialog.
- **Combine into…** — shown for Active convs. Opens the combine target picker.
- **Hide / Unhide** — toggles `meta.hidden`.
- **End conversation** — force-end. Active convs only.

Floating action button → **Spawn dialog**.

### 12.2 Page B — conversation view

Title bar shows the conversation title followed by the comma-joined member sender names in parentheses (e.g. 'Refactor (Claude-Win, Claude-WSL)'). cwd and dormant state are not shown in the title bar. When `continued_from` is set and the predecessor conversation is loaded, a slim tappable banner renders directly under the title bar reading `Continued from "<predecessor title>"`; tapping it navigates to the predecessor (multi-hop chains walk back one hop at a time). The banner is hidden when the predecessor is absent from the loaded set (aged out / not yet hydrated), so it never shows a dead affordance. The Operator dashboard surfaces the same affordance in its conversation-detail pane. (T-161; predecessor-title resolution is the shared pure helper, mirrored as `ConversationPolicy.predecessorTitle` on Android and `derive.predecessorTitle` in Operator.)

Bubble feed renders messages chronologically; right-aligned for John, left for agents, system messages styled distinctly. Markdown rendering when `format == "markdown"`. Suggestion buttons under questions. A horizontal pull (drag left/right) reveals message timestamps; pinch zooms text scale.

Reply input visible when a pending `ask_human` exists in the conv; routed via `conversations/<conv_id>/answers/<request_id>/`. Suggestion-chip taps short-circuit the typing path.

### 12.3 Spawn dialog

- **Surface** radio: Windows / WSL (WSL disabled with tooltip when `wsl_available == False`).
- **Project** MRU dropdown with per-item delete.
- **Initial prompt** free-form text, optional.
- **Conversation**: "Create new" (default) or "Add to existing" with a single-select picker of Active conversations.

Spawn button enabled when project is non-empty and a conversation option is selected. (The server applies a `COMMAND_TTL_SECONDS` freshness gate that drops stale commands, which is not a rate limit.)

### 12.4 Resume dialog

Pre-filled from the long-pressed row. Header shows title, member roster, last-activity. Body has a single optional "new prompt" field. Launching opens N terminal tabs (one per resumable member, each on its original surface), each running `claude --resume <session_id>`. All N route into a single new continuation conversation linked via `continued_from`.

### 12.5 Combine dialog

Target picker. List of every Active conversation except the long-pressed source. Single-select. Confirm dialog before merge.

### 12.6 FCM channels

Three notification channels with separate `IMPORTANCE` levels:
- **Questions** (`IMPORTANCE_HIGH`) — `ask_human` questions; banner + sound.
- **Updates** (`IMPORTANCE_DEFAULT`) — `notify_human`.
- **Documents** (`IMPORTANCE_DEFAULT`) — `send_document_human`.

FCM tap deep-links to Page B for the referenced conversation.

### 12.7 Wear OS surface

Companion Wear app shows a conversation list (filtered to non-hidden) and supports voice-dictation replies. It does NOT read agent-status (F-88). It reads the same conversation-id model as the phone (shared MainViewModel.conversationRows), filtered to non-hidden conversations via partitionConversationsForWatch.

---

## 13. Operational

### 13.1 Service management

NSSM wraps the Python server as a per-user service. Scripts in `scripts/`:
- `install-service.ps1` — installs the service, sets logs to `logs/nssm-stdout.log` / `nssm-stderr.log`, configures auto-restart.
- `restart-service.ps1` — restarts with a pytest gate.
- `uninstall-service.ps1` — removes the service.
- `register-spawn-task.ps1` — registers the `SwitchboardSpawn` scheduled task (one-time, elevated).

### 13.2 Logging

- `logs/switchboard.jsonl` — structured JSONL audit log. Every tool call, resolution, spawn, and surface-error event records here with `(conversation_id, sender, request_id?)` correlation.
- `logs/sessions/<conversation_id>_<YYYYMMDD_HHMMSS>.log` — per-conversation human-readable transcript. The timestamp suffix is the server-process start time captured once at startup, so each server restart begins a fresh transcript file per conversation.
- `logs/nssm-stdout.log` / `logs/nssm-stderr.log` — current process stdout/stderr. NSSM rotates these to timestamped filenames when a log reaches ~5 MB (AppRotateBytes 5242880, AppRotateOnline 1), online while the service runs.

### 13.3 Plugin install

Switchboard ships as a Claude Code plugin. From any Claude Code session:

```
/plugin marketplace add C:/Work/switchboard
/plugin install switchboard@switchboard
```

The plugin install wires the skill and four hooks. The MCP server connection itself is bootstrapped separately per-host (chezmoi dotfiles, or `claude mcp add switchboard --scope user --transport http http://<host>:9876/mcp --header "Authorization: Bearer <SWITCHBOARD_TOKEN value>"` for non-loopback hosts). WSL agents require bridge networking (not mirrored), `SWITCHBOARD_HOST=0.0.0.0` on the server, and a Windows firewall inbound rule for TCP 9876 from the WSL subnet. A non-loopback bind also requires `SWITCHBOARD_TOKEN` set: the server refuses to start without one (REV-003 fail-closed), and non-loopback clients must send `Authorization: Bearer <token>` on every route except `/healthz`.

WSL agents also need env vars pointing their hook scripts at the Windows host (the IP from `/etc/resolv.conf` or `ip route show default | awk '{print $3}'`):

- `SWITCHBOARD_BASE_URL` -- the three HTTP hooks (`agent-status-hook.py` -> `/agent_status`, `turn-end-hook-away-mode.py` -> `/away-mode`, `cli-session-start-hook.py` -> `/session_start`).
- `SWITCHBOARD_TOKEN` -- the same three hooks; attached as `Authorization: Bearer <token>` when set. Required for WSL agents once the server enforces a token.
- `SWITCHBOARD_MARKER_DIR` -- `cli-session-end-hook.py` (marker-file path, not HTTP).

Gemini CLI gets a separate AfterAgent hook installed via `scripts/install-turn-end-hook.ps1`.

---

## 14. Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SWITCHBOARD_HOST` | No | `127.0.0.1` | HTTP bind address (`0.0.0.0` for WSL-reachable). |
| `SWITCHBOARD_PORT` | No | `9876` | HTTP port. |
| `SWITCHBOARD_TIMEOUT_SECONDS` | No | `86400` | `ask_human` and `message_and_await_agent` block timeout. |
| `SWITCHBOARD_LOG_PATH` | No | `./logs/switchboard.jsonl` | Audit log path. |
| `FIREBASE_DATABASE_URL` | Yes | — | Firebase Realtime Database URL. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | — | Absolute path to service account key. |
| `FIREBASE_STORAGE_BUCKET` | No | — | Storage bucket hostname (document delivery). |
| `SWITCHBOARD_WINDOWS_SPAWN_ROOT` | For spawn | — | Windows project root (e.g. `C:\Work`). Alias: `SWITCHBOARD_SPAWN_ROOT`. |
| `SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT` | No | `work` | Segment appended to resolved WSL home for WSL project paths. |
| `SWITCHBOARD_WSL_HOME` | No | (probed via wsl.exe) | Escape hatch overriding the resolved WSL home path; first-priority source in resolve_wsl_home; used when the NSSM service runs in Session 0 where the wsl.exe probe fails. |
| `SWITCHBOARD_RATE_LIMIT` | No | `30` | Per-conversation rate limit for `ask_human` + `notify_human` + `send_document_human` (tokens/min). |
| `SWITCHBOARD_TOKEN` | For non-loopback | — | Shared-secret for the Bearer gate; required when `SWITCHBOARD_HOST` is non-loopback (server refuses to start without it). Loopback callers and `/healthz` are exempt. |
| `SWITCHBOARD_ROUTE_RATE_LIMIT` | No | `600` | Coarse per-route rate limit for the unauthenticated POST routes (tokens/min per route; `0` disables). |

---

## 15. Constraints

### 15.1 Reliability

- **Restart loses in-flight wait queues.** Blocked agents time out at the MCP 24h limit on their next call.
- **Stale-alive members** — process death without `SessionEnd` (SIGKILL, BSOD, network loss) leaves a member as alive from the server's view. Their tool calls time out at 24h; the conversation stays "stuck" until force-ended.
- **Best-effort `SessionEnd`** — orderly exits fire; abrupt deaths don't.

### 15.2 Scope

- **Single-host.** Conversation IDs are local to one server. Cross-host A2A is not implemented.
- **Single-distro WSL.** The default `wsl.exe` distro is used; multi-distro setups requiring `--distribution` aren't supported.
- **Claude-only spawn.** Gemini agents can participate in conversations from a terminal but can't be spawned from the phone. Gemini's hook system also lacks the equivalent of `cli-session-injector-hook.py`, so Gemini's Switchboard access depends on the Gemini CLI side maintaining a compatible injection mechanism.
- **Single global away flag.** No per-cwd, per-conversation, or per-agent overrides.

### 15.3 Security

- **Layered network exposure control.** Loopback bind (`SWITCHBOARD_HOST=127.0.0.1`) by default keeps the server unreachable off-host. A non-loopback bind (`0.0.0.0`, for WSL) requires `SWITCHBOARD_TOKEN`: `load_config` raises `ConfigError` at startup if it's unset (REV-003 fail-closed). Once set, `TokenAuthMiddleware` gates every route except `/healthz` behind `Authorization: Bearer <token>`, exempting loopback peers regardless of bind. The WSL-subnet firewall rule remains recommended as defense-in-depth, not the enforced control.
- **No sandboxing.** Spawned agents run with `--dangerously-skip-permissions`; safety is governed by the agent's `SKILL.md` instructions, which gate destructive actions behind `ask_human`. Switchboard enforces the protocol (no terminal leaks while away), not the execution.
- **Document path validation.** `send_document_human` runs a secret-name denylist first (`.env`, `service-account.json`, `credentials.json` exact; `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env` globs), then an extension allowlist (`.md` `.markdown` `.txt` `.log` `.csv` `.tsv` `.diff` `.patch` `.pdf` `.png` `.jpg` `.jpeg` `.gif` `.webp`; extensionless files refused). Path-traversal (`..`) rejected. 5 MB cap. SHA-256 logged.
- **Rate limiting.** `ask_human` + `notify_human` + `send_document_human` bucket per conversation, default 30 tokens/min.

### 15.4 Modalities

- **Text-heavy.** Outbound document delivery via `send_document_human`. Inbound phone-side input is text-only — no multimodal injection (screenshots, audio) into a session.
- **Request/response model.** `ask_human` is a synchronous blocking call. No streaming or duplex transport.

---

## 16. Related repos and docs

- `skills/switchboard/SKILL.md` — agent-facing protocol reference (away-mode rules, tool signatures, conversation lifecycle hints).
- `CLAUDE.md` (project root) — agent orientation and project tour for working in this repo.
- `docs/superpowers/specs/` — dated design history. Not authoritative for current behavior; preserved for the reasoning trail.
