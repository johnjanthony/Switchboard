# Unified Channel Routing Design

**Date:** 2026-04-22
**Status:** Approved

---

## 1. Problem Statement

The current design routes messages using `agent_id`, which creates three problems:

1. **Collab sessions produce 3 tabs** — relay messages land in a session tab, but each agent's `ask_human` calls create their own separate agent tabs, splitting a single conversation across three views.
2. **Single agents drift** — agents sometimes vary the `agent_id` they pass between calls, splitting their messages across multiple tabs.
3. **Fragmented log files** — a collab session with 2 agents generates 3 log files (one per agent_id + one session log).

---

## 2. Solution Overview

Replace `agent_id` with two orthogonal concepts:

- **`channel_id`** — routing key. Determines which tab, Firebase path, and log file a message belongs to. Stable, set at spawn time, never varies.
- **`sender`** — display label. Appears in the chat bubble. Can be descriptive. Defaults to `"Claude"`.

Every tool call uses the same `channel_id` for the lifetime of a session, regardless of tool type. This collapses the 3-tab collab problem to 1 tab and eliminates single-agent drift.

---

## 3. Channel ID Format

Both single-agent and collab sessions use:

```
{project_key}-{YYYYMMDD}-{HHmmSS}   (UTC at spawn time)
```

Examples:
- `switchboard-20260422-143052`
- `rpdm-20260422-091500`

Every spawn generates a new unique `channel_id`. The project key provides human readability; the datetime provides uniqueness and sortability. Both session types use identical format — no visual distinction needed.

---

## 4. Revised Tool Signatures

```python
ask_human(
    question:     str,
    channel_id:   str,
    sender:       str = "Claude",
    format:       str = "plain",
    suggestions:  list[str] | None = None,
) -> str

notify_human(
    message:    str,
    channel_id: str,
    sender:     str = "Claude",
    format:     str = "plain",
) -> str

send_document_human(
    path:       str,
    channel_id: str,
    sender:     str = "Claude",
    caption:    str | None = None,
) -> str

message_and_await_agent(
    channel_id: str,
    sender:     str,
    message:    str | None = None,
) -> str
```

`agent_id` is removed from all tool signatures. `message_and_await_agent` previously took `session_id` + `agent_id`; both are replaced by `channel_id` + `sender`.

---

## 5. Message Type Taxonomy

`message_type` is assigned server-side (not a tool parameter). It drives rendering in the Android app.

| Tool | message_type | Android rendering |
|---|---|---|
| `ask_human` | `"question"` | Highlighted bubble + reply input |
| `notify_human` | `"notify"` | Regular bubble |
| `message_and_await_agent` | `"agent"` | Regular bubble (collab channels only) |
| `send_document_human` | `"document"` | Document bubble with tappable link |

---

## 6. Firebase Schema

### Unified message path

All messages from all tools write to:

```
sessions/{channel_id}/messages/{msg_id}
```

Message node fields:

```json
{
  "sender":       "Claude",
  "message_type": "question",
  "content":      "Should I overwrite foo.py?",
  "request_id":   "a1b2c3d4",
  "timestamp":    1745327452000
}
```

`request_id` is present only for `message_type="question"`. All other fields are always present.

### Session meta node

Written at spawn time for both session types. Tab appears in the Android app as soon as meta is written — before any messages arrive.

Single-agent session:
```json
{
  "type":        "single",
  "project_key": "switchboard",
  "created_at":  1745327452000
}
```

Collab session:
```json
{
  "type":          "collab",
  "project_key":   "switchboard",
  "agent_senders": ["Agent 1", "Agent 2"],
  "task":          "Perform a technical review...",
  "created_at":    1745327452000
}
```

### Unchanged paths

| Path | Purpose |
|---|---|
| `responses/{request_id}` | ask_human reply resolution — untouched |
| `commands/{id}` | Spawn commands — untouched |
| `sessions/{channel_id}/inject_queue` | Collab human injection — untouched |

### Abandoned paths (no new writes)

`questions/`, `notifications/`, `documents/`, `sessions/{agent_id}/state`

---

## 7. Server Changes

### `server/gateway.py`

- `ask_human`, `notify_human`, `send_document_human`: replace `agent_id` param with `channel_id` + `sender`. All write to `sessions/{channel_id}/messages/` via `backend.write_channel_message(channel_id, sender, message_type, content, request_id=None)`.
- `ask_human`: `registry.get_session_for_agent(agent_id)` lookup removed. Replaced by `registry.get_session(channel_id)` to check whether this is a collab channel (for relay gating).
- `message_and_await_agent`: `session_id`/`agent_id` params renamed to `channel_id`/`sender`.
- `_append_session_log`: keyed by `channel_id`. One file per channel, all message types interleaved.

### `server/collab.py`

- `agent_ids: tuple[str, str]` → `agent_senders: tuple[str, str]` (e.g. `("Agent 1", "Agent 2")`)
- `_waiting` and `_pending` dicts already key by string — no structural change
- `other_agent(agent_id)` → `other_sender(sender)`
- Routing within a session: "deliver to the other" = deliver to whichever sender is not the calling sender. With exactly 2 agents this is unambiguous.

### `server/registry.py`

- `_agent_to_session: dict[str, str]` removed
- `get_session_for_agent(agent_id)` removed
- `add_session` no longer maintains `_agent_to_session` index

### `server/firebase.py`

- `send_question(request_id, agent_id, ...)` → `write_channel_message(channel_id, sender, "question", content, request_id=request_id)` + FCM push
- `send_notification(agent_id, message, format)` → `write_channel_message(channel_id, sender, "notify", message)` + FCM push
- `send_document(agent_id, path, caption)` → `write_channel_message(channel_id, sender, "document", url, caption=caption)` + FCM push
- `write_session_message(session_id, agent_id, msg_type, content, ...)` → replaced by unified `write_channel_message`
- `write_session_meta` extended to accept `type="single"|"collab"` and write the appropriate meta shape

### `server/messenger.py`

ABC updated to replace `send_question`, `send_notification`, `send_document`, `write_session_message` with `write_channel_message`. `write_session_meta` signature updated.

### `server/spawn.py`

Both `_handle_single_spawn` and `_handle_collab_spawn`:

```python
channel_id = f"{project_key}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
```

`_handle_single_spawn`:
- Generates `channel_id` as above
- Calls `backend.write_session_meta(channel_id, type="single", project_key=project_key)`
- Includes `channel_id` in spawn prompt

`_handle_collab_spawn`:
- Triggered by `--collab` flag (always relay=True; `--agents=N` and `--relay` flags removed)
- Generates `channel_id` as above (replaces `session_id = f"{project_key}-{secrets.token_hex(4)}"`)
- `agent_senders = ["Agent 1", "Agent 2"]` (replaces `agent_1_id`/`agent_2_id`)
- `backend.write_session_meta(channel_id, type="collab", agent_senders=agent_senders, task=task)`
- Spawn prompts include `channel_id` and `sender`

Updated spawn prompt instructions:

- **Single agent:** *"Your channel_id is `{channel_id}`. Use it for every tool call. sender defaults to 'Claude' unless you were given a different name."*
- **Collab agent:** *"Your channel_id is `{channel_id}`. Your sender is `Agent {N}`. Use both for every tool call."*

---

## 8. Android Changes

### Data models (`network/ApiService.kt`)

`Question`, `CollabMessage`, `CollabSession`, `CollabSessionMeta` replaced by:

```kotlin
data class ChannelMessage(
    var sender: String = "",
    var message_type: String = "",   // "question"|"notify"|"agent"|"document"
    var content: String = "",        // message text; for documents: caption/filename
    var url: String? = null,         // download URL, present only for "document" type
    var request_id: String? = null,  // present only for "question" type
    var timestamp: Long = 0L
)

data class Channel(
    val channelId: String,
    val type: String,                // "single"|"collab"
    val projectKey: String,
    val agentSenders: List<String> = emptyList(),
    val messages: MutableList<Pair<String, ChannelMessage>> = mutableListOf()
)
```

### `MainViewModel.kt`

State:
```kotlin
val channels: StateFlow<Map<String, Channel>>
val pendingQuestions: StateFlow<Map<String, Pair<String, ChannelMessage>>>
// channel_id → (msg_id, message) for the current unanswered question per channel
```

Removed state: `_questions`, `_notifications`, `_documents`, `_collabSessions`, `_pendingSessionQuestions`, `_agents`, `_selectedAgentId`.

`setupFirebase()` question/notification/document listeners removed. `setupSessionsListener()` replaced by `setupChannelsListener()` which watches `sessions/`. On `onChildAdded`, reads `meta` to build a `Channel`, then attaches a `messages` child listener for live updates. One listener, one code path for both session types.

`replyToQuestion(channelId, msgId, requestId, text)` — writes to `responses/{requestId}`, removes question from `pendingQuestions[channelId]`.

`sendInjectMessage(channelId, text)` — writes to `sessions/{channelId}/inject_queue` (collab only; disabled for single channels in UI).

`spawnSession` updated to pass `channel_id`-aware command format.

### `MainActivity.kt`

**Tabs:** Keyed by `channel_id`. Label is the `channel_id` string. All legacy agent-list and session-tab distinction removed.

**`ChannelView` composable** (replaces `ChatView` + `SessionChatView`):
- `LazyColumn` over `channel.messages` sorted by timestamp
- Rendering by `message_type`:
  - `"question"` → highlighted bubble (error container color + border) + sender label
  - `"notify"` / `"agent"` → regular bubble + sender label
  - `"document"` → document bubble with tappable download link
- Compose area:
  - **Collab channel** — always enabled; sticky reply banner when a question is pending, plain inject input otherwise
  - **Single channel** — only shown when a question is pending (reply only, no inject)

---

## 9. SKILL.md Changes

Tool signatures updated throughout. `agent_id` removed from all examples and descriptions.

Choosing a `channel_id` section updated:

> `channel_id` is provided in your spawn prompt. Use it for every tool call — `ask_human`, `notify_human`, `send_document_human`, and `message_and_await_agent`. Do not derive or vary it.

`sender` section added:

> `sender` is your display name in the conversation. Defaults to `"Claude"`. In collab sessions you are told your sender (`"Agent 1"` or `"Agent 2"`) in your spawn prompt.

---

## 10. Collab Mode — Spawn UI and Command

Collab sessions are limited to exactly 2 agents. This is explicit and permanent, not a current-limitation footnote.

**Android spawn dialog:** The agents spinner (`−`/`+` stepper) and relay checkbox are removed. Replaced by a single **Collab mode** checkbox (default unchecked). When checked, the relay checkbox that was previously separate is implicitly enabled — collab mode always relays. The dialog becomes:

- Project (optional)
- Initial Prompt / Instructions
- ☐ Collab mode

**Spawn command flag:** `--agents=2 --relay` is replaced by `--collab`. The existing `--agents=N` and `--relay` flags are removed from `_parse_spawn_flags`. `--collab` sets both `agents=2` and `relay=True` in one flag.

**`SpawnHandler`** routes:
- No flag → `_handle_single_spawn`
- `--collab` → `_handle_collab_spawn` (always relay=True)
- Any `--agents=N` → error: unsupported, use `--collab`

**`spawnSession` in `MainViewModel.kt`** updated to:
```kotlin
fun spawnSession(project: String, prompt: String, collab: Boolean = false) {
    val flags = if (collab) " --collab" else ""
    val command = if (project.isBlank()) "/spawn$flags $prompt"
                  else "/spawn $project$flags $prompt"
    commandsRef.push().setValue(command)
}
```

---

## 11. Out of Scope

- Tab archival / deletion (old channels accumulate in Firebase and Android)
- Inject support for single-agent channels
- More than 2 agents per collab session
- Changing `responses/` or `commands/` Firebase paths
