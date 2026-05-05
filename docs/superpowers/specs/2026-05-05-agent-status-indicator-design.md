# Agent Status Indicator — Design

**Date:** 2026-05-05
**Branch context:** `develop` (post-streaming-cleanup)
**Status:** design approved, ready for implementation plan

## Problem

When John is away from his desk and an agent is working on his behalf, his phone today shows him *messages* (`notify_human`, `ask_human`, etc.) but no indication of what's happening *between* messages. A long Maven build or a multi-minute thinking pass looks indistinguishable from a hung agent. He'd like a lightweight "what is it doing right now" signal on the phone, surfaced both inside the channel view and in the channel list.

This is **not** about streaming the agent's thinking content. It's about surfacing transient state transitions ("thinking", "running Bash: `npm test`", "waiting for partner") so the human knows the agent is alive and productive between visible messages.

## Goals and non-goals

### Goals

- Surface ephemeral, current-only state. No history, no log, no scrollback.
- Use Claude Code's existing hook system as the source of truth — automatic, granular, free.
- **Only write status updates when John is in away mode** for the cwd. At-desk events are silently dropped — when John is at the terminal he's reading the live conversation, not the phone status row, so the Firebase write would be pure cost with no observer.
- Disambiguate sender in collab sessions where two named agents share a channel.
- Replace the current pending-question pulsing dot in the message bubble so the new active-state pulsing dot in the channel list doesn't visually conflict with it.
- Refresh the app's primary color palette from the Material 3 default purple to a blue family that matches the visual treatment used in design mockups.

### Non-goals

- Streaming the agent's thinking text (extended-thinking deltas). Investigated and shelved — would require replacing Claude Code with a custom Agent SDK runner.
- Hook coverage for non-Claude-Code agents (Gemini). Future expansion; the design accommodates additive Gemini hook scripts later without server-side changes.
- Per-tool granular detail beyond a single-line summary (no expanded args, no progress bars, no live stdout).
- Persistence of status history. The message log already records what tools ran via the existing message-stream pattern.

## Architecture

```
┌──────────────────┐     hook event JSON     ┌──────────────────────┐
│   Claude Code    │ ───────────────────────▶│  agent_status_hook   │
│  (hooks fire on  │                         │   (Python script,    │
│   each event)    │                         │    1s timeout)       │
└──────────────────┘                         └──────────┬───────────┘
                                                        │ HTTP POST
                                                        │ (fail-open)
                                                        ▼
┌──────────────────────────────────────────────────────────────────┐
│                  Switchboard FastMCP server                       │
│                                                                   │
│  POST /agent_status                                               │
│      ├─▶ canonicalize cwd                                         │
│      ├─▶ if not is_away_mode_active(cwd): return  ← gate          │
│      ├─▶ resolve sender:                                          │
│      │     1. baton holder (if cwd in active collab)              │
│      │     2. last messaging-call sender for cwd                  │
│      │     3. fallback "Claude"                                   │
│      └─▶ backend.write_agent_status(...)                          │
└──────────────────────────────────────────────────────────────────┘
                                                        │
                                                        ▼
                                  ┌──────────────────────────────────┐
                                  │  channels/{cwd_key}/agent_status │
                                  │    {sender, state, detail,       │
                                  │     updated_at}                  │
                                  └──────────────┬───────────────────┘
                                                 │ Firebase listener
                                                 ▼
                                  ┌──────────────────────────────────┐
                                  │      Android phone client        │
                                  │   (in-channel inline row +       │
                                  │    channel-list leading dot)     │
                                  └──────────────────────────────────┘
```

The hook is fire-and-forget. The server returns 200 immediately regardless of backend success. The phone reflects whatever's in Firebase, with a 30-minute recency check to handle zombie state from agent crashes.

## State model

Two first-class active states, one terminal "clear" sentinel, and idle as the absence of recent state.

| State | Meaning | When emitted |
|---|---|---|
| `thinking` | Between user prompt and first tool call, or between tool calls | `UserPromptSubmit`, `PostToolUse` |
| `tool:<tool_name>` | Actively running a tool. `detail` carries a short summary | `PreToolUse` for any tool not in special-case sets |
| `waiting` | Blocked on a peer agent's reply | `PreToolUse` for `mcp__switchboard__message_and_await_agent` |
| `clear` | Sentinel; server deletes the field. Phone renders idle | `Stop`, `PreToolUse` for `mcp__switchboard__ask_human` |

**Why `ask_human` clears rather than waits:** the message bubble's pending-question indicator (an outlined ring with `?`) already conveys "agent is blocked on John's reply." A status field saying `waiting` would be redundant clutter.

**Why `message_and_await_agent` waits rather than clears:** there's no equivalent message-bubble cue for partner-blocked. The status field is the only signal the human has. In collab, the `waiting` write is overwritten within milliseconds by the partner's first hook event after the baton flips, so it's effectively transient.

**Idle = absence.** When the field is missing or older than the recency threshold, the phone renders nothing. No explicit `idle` state.

## Sender resolution

The hook script doesn't include a `sender` in its POST. The server resolves it:

1. **Active collab session?** Use the baton holder. The Registry already tracks collab enrollment and the live baton state for `message_and_await_agent` routing — this design adds no new state to support it, just a read accessor.
2. **Else, most recent sender to call any messaging tool in this cwd.** New `Registry` field `last_messaging_sender: dict[cwd, sender]`, populated by every `notify_human` / `ask_human` / `send_document_human` / `message_and_await_agent` handler.
3. **Else, fall back to `"Claude"`.** Cold-start path; self-corrects on the agent's first messaging call.

This avoids the need to bridge Claude Code's internal `session_id` to the agent's `sender` — those identifiers don't share a known mapping (Claude Code does not expose its session_id to the agent, and the MCP `Mcp-Session-Id` is a separate, MCP-protocol-level identifier). Switchboard's existing collab-baton tracking is enough because hooks only fire for the currently-active agent.

## Away-mode gating

The handler short-circuits before the backend write when `cwd` is not in away mode:

```python
if not registry.is_away_mode_active(canonical):
    return
```

Rationale: when John is at the terminal, he's reading the live conversation; the phone status row has no observer. Writing to Firebase on every tool boundary in that case is pure cost — RTDB writes, listener traffic, log noise — for no benefit. The gate is the cleanest place for this decision because the server has fast in-memory access to away-mode state via the existing `Registry.is_away_mode_active(cwd)` accessor (which already implements per-cwd-override-beats-global precedence).

**Where the gate lives.** Server-side, not hook-side. The hook stays dumb — it always POSTs. Two reasons:

1. **HTTP traffic.** Hook-side gating would require `GET /away-mode?cwd=…` before each post, doubling outbound traffic for events that already fire dramatically more often than the existing `Stop` hook (every prompt, every tool boundary).
2. **Reversibility.** Toggling the gate is two server-side lines; no hook-script update on the user's machine.

**Behavior at mode transitions:**

- **At-desk → away.** Hooks have been firing all along; server now starts honoring the writes. The first event after the transition lights up the indicator.
- **Away → at-desk.** Server stops writing new updates. The last `agent_status` value sits in Firebase until the phone-side recency check ages it out (30 minutes). Acceptable: by the time John has returned to the terminal, he's not looking at the phone anyway.

**Cold-start interaction with sender resolution.** The gate runs *before* sender resolution, so no `record_messaging_sender` writes are missed by the gate (they happen in the messaging-tool handlers, which are independent of `handle_agent_status`). The cold-start fallback to `"Claude"` is unchanged.

## Hook script

Single Python script, registered against four lifecycle events. Lives in the switchboard repo at `scripts/agent-status-hook.py`, matching the kebab-case naming and location of the existing `scripts/turn-end-hook-away-mode.py`.

**Behavior by event:**

```python
CLEAR_TOOLS = {"mcp__switchboard__ask_human"}
WAITING_TOOLS = {"mcp__switchboard__message_and_await_agent"}

# UserPromptSubmit  → POST(state="thinking")
# Stop              → POST(state="clear")
# PostToolUse       → POST(state="thinking")
# PreToolUse:
#   if tool_name in CLEAR_TOOLS:    POST(state="clear")
#   elif tool_name in WAITING_TOOLS: POST(state="waiting")
#   else:                            POST(state=f"tool:{tool_name}",
#                                         detail=build_detail(tool_name, tool_input))
```

**Detail extraction (`build_detail`):**

| Tool | Detail |
|---|---|
| `Bash` | First 200 chars of `tool_input.command` |
| `Edit`, `Write`, `Read`, `NotebookEdit` | Basename of `tool_input.file_path`, first 200 chars |
| `WebFetch` | Domain of `tool_input.url` (parsed via `urlparse`), first 200 chars |
| `Glob`, `Grep` | First 200 chars of `tool_input.pattern` |
| Anything else | `null` |

The 200-char cap matches the server-side write cap and exists only as an outbound-payload safety net (avoid sending a multi-KB Bash command). The phone client is responsible for the *visual* truncation: `AgentStatusRow`'s `Text` uses `maxLines = 1` + `overflow = TextOverflow.Ellipsis`, so the rendered string ellipsizes based on the actual row width. On a narrow phone the ellipsis kicks in at ~40 visible characters; on a Pixel Fold unfolded it shows much more. No device-detection code — falls out of the layout system. (Earlier iteration capped detail at 40 chars in the hook for "phone rendering compactness," which truncated information before it ever reached the UI; relaxed to 200 once the Pixel Fold form factor exposed the lossy aggression.)

**Failure model:**
- 1-second HTTP timeout
- Any error (URLError, TimeoutError, malformed stdin, JSON parse failure) → swallow, exit 0, no stdout. Identical to the existing `turn-end-hook-away-mode.py` fail-open pattern.
- Hook is registered separately from the existing turn-end hook (Option A from design discussion). They run independently on `Stop`; the status hook emits no decision JSON, so the away-mode block decision is unaffected.

## Server changes

### New HTTP endpoint

`POST /agent_status` on the existing FastMCP HTTP app (alongside the MCP transport endpoints `/mcp` and the existing `/away-mode`, `/collab-partner-state` GETs). Body:

```json
{
  "cwd": "C:\\Work\\switchboard",
  "state": "tool:Bash",
  "detail": "npm test"
}
```

- `state` is a required non-empty string. Allowed values: `thinking`, `waiting`, `clear`, or any string starting with `tool:`.
- `detail` is optional. If longer than 200 chars, the server truncates to 200 (silently, no error). Truncation rather than rejection avoids dropping the entire status update because of a verbose Bash command or long file path.
- `cwd` is canonicalized server-side via the existing utility.
- Returns 200 with empty body on both success and backend failure (fire-and-forget contract).

### Handler (in `server/gateway/handlers.py`)

```python
async def handle_agent_status(cwd: str, state: str, detail: str | None) -> None:
    canonical = canonicalize_cwd(cwd)
    sender = resolve_sender_for_cwd(canonical)
    await backend.write_agent_status(canonical, sender, state, detail)
```

`resolve_sender_for_cwd` consults the Registry per the sender resolution rules above.

### `Registry` extension

Two additions:
- `last_messaging_sender: dict[cwd, sender]` — populated by every messaging tool handler before it returns.
- A read accessor `get_collab_baton_holder(cwd) -> sender | None` — returns the currently-active sender if `cwd` has an active collab session, else `None`. Wraps existing collab-session state.

### Backend method

New abstract method on `MessageWriter`:

```python
async def write_agent_status(
    self, cwd: str, sender: str, state: str, detail: str | None
) -> None:
    """No-op default; FirebaseBackend overrides."""
    pass
```

`FirebaseBackend.write_agent_status`:
- If `state == "clear"`: delete `channels/{key}/agent_status`
- Else: set `channels/{key}/agent_status = {sender, state, detail, updated_at: ServerValue.TIMESTAMP}`

## Android changes

All in `android/app/src/main/java/io/github/johnjanthony/switchboard/`. Wear app untouched.

### Data model

`network/Models.kt`:

```kotlin
data class AgentStatus(
    val sender: String,
    val state: String,        // "thinking" | "waiting" | "tool:<name>"
    val detail: String?,
    val updatedAt: Long       // epoch ms
)
```

`Channel` gains `val agentStatus: AgentStatus? = null`.

### Firebase subscription

The existing per-channel listener is extended to read the new `agent_status` subnode. No new connection; same listener picks up the new field.

### In-channel rendering

New composable `AgentStatusRow` rendered as **always the last item** in `SessionViewScreen.kt`'s `LazyColumn` when `channel.agentStatus != null && (now - updatedAt) < 30.minutes`. Implementation requirement: render the row in a separate `item { }` block placed *after* the existing `items(messages)` block, so any newly-arriving message (an agent's `notify_human`, John's response to a question, etc.) is appended *before* the status row, leaving the status row's "last in list" position invariant.

- Italic, faded text in `MaterialTheme.colorScheme.primary`
- Pulsing-dot icon (filled circle, halo animation) on the leading edge
- Format: `"<sender> · thinking"` or `"<sender> · running <tool>"` or `"<sender> · waiting"`
- For `tool:<name>` with detail: `"<sender> · running <tool>: <detail>"`

Mimics the Slack/Messenger "is typing…" pattern.

### Channel-list rendering

Modify `SessionRowComposable.kt` to add a leading-edge slot before the existing title `Column`:

- Fixed-width container (~14dp wide)
- When `channel.agentStatus` is fresh: render an animated pulsing dot (filled circle, expanding halo) in `MaterialTheme.colorScheme.primary`
- When idle: empty (preserves the leading-edge column to avoid title shift between active/idle)

The existing trailing-edge unread badge is unchanged.

### Pending-question indicator swap

Modify `MessageBubble.kt` lines 209-216. Replace the filled circle with an outlined ring containing a `?` glyph, using the same expanding-halo animation as the channel-list active dot.

The two pulsing-ring usages form a deliberate visual family:
- **Channel-list active dot** — solid filled circle inside halo (reads as "glowing dot")
- **Pending-question marker** — outlined circle with `?` inside halo (reads as "glowing question mark")

Same color, same rhythm, different glyph. "Things drawing attention" share a family without conflation.

### Color refresh

`theme/Color.kt`:

```kotlin
// New blue palette (locked during spec review)
val DarkPrimaryBlue            = Color(0xFF6CB8FF)  // bright sky blue — primary accent
val DarkPrimaryContainerBlue   = Color(0xFF1F3656)  // deep navy — bubble background
val DarkOnPrimaryBlue          = Color(0xFF000000)  // text on bright accent
val DarkOnPrimaryContainerBlue = Color(0xFFCFE2FF)  // text on navy bubble
```

`theme/Theme.kt`:

```kotlin
private val DarkColorScheme = darkColorScheme(
    primary             = DarkPrimaryBlue,
    primaryContainer    = DarkPrimaryContainerBlue,
    onPrimary           = DarkOnPrimaryBlue,
    onPrimaryContainer  = DarkOnPrimaryContainerBlue,
    // ... rest unchanged
)
```

This affects four call sites (all in `MessageBubble.kt`):
- Human bubble background (`primaryContainer`)
- Human bubble text (`onPrimaryContainer`)
- Selection border (`primary`)
- The old pending dot (going away in this same change)

The new active-state pulsing dot and the new pending-`?` ring both use `colorScheme.primary`, automatically picking up the new blue.


## Recency / idle threshold

Phone-side check: `(now - agentStatus.updatedAt) < 30 * 60 * 1000`. If older, render as idle.

The 30-minute window is a generous default chosen to cover long single-tool calls (Maven builds, npm installs, large WebFetches) where no hook fires between `PreToolUse` and `PostToolUse`. The primary clear signal is the `Stop` hook explicitly setting `state="clear"`; recency is the safety net for crashes where `Stop` never fires.

If long tool calls regularly exceed 30 minutes, the threshold can be raised without any other change. Alternatively, a future enhancement could add a heartbeat sidecar during `PreToolUse` that pings the server every 30s to refresh `updated_at` while the tool runs — out of scope here.

## Error handling

| Failure | Behavior |
|---|---|
| Switchboard server unreachable | Hook fail-opens (`URLError` → exit 0). Status doesn't update; recency check eventually marks stale |
| Hook script crash (malformed stdin, etc.) | Exit non-zero with stderr; Claude Code logs but doesn't surface. Next event corrects |
| HTTP timeout (1s on localhost) | Same fail-open path |
| Server-side handler exception | Logged; returns 200 anyway. Hook never sees the failure |
| Backend (Firebase) write fails | Logged server-side; returns 200. Status doesn't update; corrects on next event |
| Server starting up when hook fires | Connection refused → fail-open exit 0 |
| Concurrent writes | RTDB serializes; last-writer-wins matches the ephemeral model |
| Cold-start sender ambiguity | Falls back to `"Claude"`; self-corrects on first messaging call |
| Phone listener disconnect | Existing channel listener handles reconnect; new field rides along |
| Stale data on app launch | Recency check → render idle |

## Hook ordering on `Stop`

The new `agent_status_hook` and the existing `turn-end-hook-away-mode.py` both register against `Stop`. Per Claude Code's hooks schema, multiple matchers per event are supported and run independently; each processes its own stdout. The status hook emits no decision JSON (just exits 0 after the HTTP call), so it cannot suppress or override the away-mode block decision.

## Testing

### Server-side unit tests (Python, pytest)

Mirror the existing `tests/test_gateway_notify_human.py` pattern with the `RecordingBackend` fixture, extended to capture `write_agent_status` calls. All happy-path tests enable away-mode for their test cwd via `registry.update_cwd_override_cache(canonical, True)` since `handle_agent_status` is gated on it.

- `test_resolves_sender_from_recent_messaging_call`
- `test_falls_back_to_claude_when_no_messaging_history`
- `test_uses_collab_baton_holder`
- `test_clear_state_passes_through` — `state="clear"` is forwarded to backend (which translates to delete)
- `test_canonicalizes_cwd`
- `test_swallows_backend_exception`
- `test_truncates_oversized_detail` — POST with 500-char detail; assert backend received exactly 200 chars
- `test_skips_write_when_not_in_away_mode` — gate behavior: at-desk events are silently dropped
- `test_writes_when_global_away_mode_active` — gate behavior: global away flag is sufficient
- `test_per_cwd_override_false_beats_global_away` — gate behavior: per-cwd override at False overrides global True

### Hook script unit tests

Pure-Python; mock `urllib.request.urlopen` and assert the POST body.

- `test_user_prompt_submit_sends_thinking`
- `test_pre_tool_use_for_bash_includes_command_detail`
- `test_pre_tool_use_for_edit_includes_filename_detail`
- `test_pre_tool_use_for_ask_human_sends_clear`
- `test_pre_tool_use_for_message_and_await_agent_sends_waiting`
- `test_post_tool_use_sends_thinking`
- `test_stop_sends_clear`
- `test_connection_refused_exits_zero`
- `test_timeout_exits_zero`
- `test_malformed_stdin_exits_zero`

### Integration tests

`tests/test_agent_status_integration.py` — exercises the full HTTP route via Starlette's `TestClient`:

- `test_post_agent_status_returns_200_and_calls_backend` — happy path, away-mode enabled, backend called.
- `test_post_agent_status_returns_200_when_at_desk_no_backend_call` — gate behavior: HTTP returns 200 even though the handler short-circuits and the backend is never called.
- `test_post_agent_status_returns_200_on_malformed_body` — non-JSON body returns 200, backend not called.
- `test_post_agent_status_returns_200_on_missing_fields` — body missing `state` returns 200, backend not called.

### Android testing

Manual via emulator. No new `@Preview` functions. Existing `PreviewMessageBubblePendingQuestion` automatically picks up the new ring `?` since the underlying `MessageBubble` composable is what changes.

### Hook-ordering verification (install-time)

Manual checklist documented in `AGENTS.md`:

1. Install both hooks via the install script.
2. Run a Claude Code session in a test cwd.
3. Type a prompt that uses one tool then asks a question:
   `"Read README.md, then ask me whether you should commit."`
4. Enter away mode mid-turn.
5. Confirm on phone:
   - Channel-list dot pulses while Claude is reading.
   - Status row cycles `thinking` → `tool:Read` → `thinking` → cleared (cleared because `ask_human` is pending and the `?` indicator takes over).
6. Confirm in terminal:
   - Stop hook fires; away-mode block engages; agent gets the redirect message.
7. Reply on phone; let Claude finish; confirm:
   - Status indicator disappears (Stop hook clears it).
   - Pending-`?` on the question bubble disappears (answered).

## Decisions confirmed during spec review (and post-implementation tweaks)

- **Color palette** locked in: `primary = #6CB8FF`, `primaryContainer = #1F3656`, `onPrimary = #000000`, `onPrimaryContainer = #CFE2FF`. Reviewed against an in-context mockup.
- **Wear app theme** stays untouched in this iteration. Recoloring the wear app is deferred to a separate change.
- **Detail truncation moved from the hook to the UI.** Originally capped at 40 chars in the hook for "phone rendering compactness." During post-implementation testing on a Pixel Fold, the 40-char hook cap was lossy on the unfolded form factor (text was already gone before the UI could decide). Relaxed the hook cap to 200 chars (matches the server's payload-safety cap) and pushed visual truncation to `AgentStatusRow`'s `Text` via `maxLines = 1` + `overflow = TextOverflow.Ellipsis`. Compose's ellipsization is intrinsically width-aware — folded narrow shows less, unfolded wide shows more, no device-detection code.
- **`AgentStatusRow` placement** is always the last item in the in-channel `LazyColumn`, rendered in a separate `item { }` block placed after `items(messages.size, …)` — newly-arriving messages append before the row, preserving its terminal position.
- **Oversized `detail` payloads** (over 200 chars) are truncated server-side rather than rejected. The hook also caps at 200 as an outbound payload sanity bound.
- **Server-side away-mode gate.** Added post-implementation. `handle_agent_status` short-circuits when `Registry.is_away_mode_active(cwd)` returns `False`. Hook stays dumb (always POSTs); the server decides whether to write. Trades one extra in-memory check on the server for zero unnecessary Firebase writes when John is at the terminal. Reversibility: two lines.

## Non-goals reconfirmed

- No streaming of thinking text.
- No persistence/log of past statuses.
- No per-tool deep introspection beyond a single short detail line.
- No coverage for non-Claude-Code agents in this iteration. The data shape is forward-compatible — additive Gemini hook scripts can be layered in later without server-side changes.
