# Conversations: Collab Redesign — Design

**Date:** 2026-05-19
**Branch context:** `develop` (post-canonicalize-cwd-POSIX commit b889472)
**Status:** design approved, ready for implementation plan

## Problem

Today's collab routing assumes both agents in a collab session share a literal cwd string. That works for the original use case (phone `/spawn --collab` puts two agents into one directory on one Windows host) but breaks the moment two agents reach the same daemon from different working directories — e.g. a Windows-native agent at `C:\Work\switchboard` and a WSL agent at `/mnt/c/Work/switchboard` (the same physical directory) or at `/home/john/work/switchboard` (a separate WSL clone). Two agents at different cwds on the same host cannot collab today, even though they're both connected to the same in-memory `Registry`.

Beyond the cross-environment case, the existing collab protocol has accumulated tension that points to a bigger reframe:

- "Channel" is overloaded: it's a Firebase persistence key, a routing identifier, the phone-side display unit, AND a notion of "agent identity for this cwd." That overload makes cross-cwd collab structurally impossible without a new abstraction.
- The implicit BYO-collab via parallel `message_and_await_agent` calls is fragile (the H8/H9/H10 turn-end hook invariants exist solely to compensate) and only ever supported same-cwd collab anyway.
- `end_collab`'s reporter-vs-listener handoff and the per-channel pill chip's interaction with global away mode add cognitive load without enabling new capabilities.

The fix is structural, not a patch: a new **Conversation** object becomes the persistence + routing unit, replacing channels in Firebase. Channels (= canonical cwds) survive only as agent identity — the thing the agent passes on every tool call. Collab becomes explicit (`enter_conversation` / `leave_conversation`), uniform across single-agent away-mode and multi-agent collab, and indifferent to which cwds the participating agents come from.

## Goals and non-goals

### Goals

- Two or more agents on **any** combination of cwds (same OS, different OS, same directory, different directories) on the same daemon can collab, joined by an explicit handshake rather than a coincident cwd.
- Single-agent away-mode interactions and multi-agent collab use the same conversation primitive — one mental model for both.
- The talking-stick FIFO is deadlock-free by construction: the seed of a brand-new conversation blocks until a second agent arrives, so a speak event can never fire with an empty wait queue.
- Existing message + channel UX fields (markdown rendering, suggestion buttons, document attachments, request_id linkage, opened/cancelled/rejected flags, agent status, pending-response badges) carry forward verbatim onto conversations. Nothing visible to the human on the phone regresses.
- Cross-host collab (T-025) remains explicitly out of scope, but the conversation abstraction is the right place to add a Firebase-transport hop later if needed.

### Non-goals

- **Spawn redesign.** The Android spawn dialog + collision handling presupposes a per-cwd notion of "active channel" that doesn't survive this redesign. Spawn is removed from the Android client in this work; server-side spawn code is preserved (minimal touch for compile). Redesigning spawn around conversations — especially around session-resume — gets its own brainstorm (new backlog item T-027).
- **Cross-host / cross-daemon A2A** (T-025). Two daemons on two machines is still out of scope. Conversation IDs are local to a daemon; no Firebase-transport synchronization between daemons.
- **Per-channel or per-conversation away-mode overrides.** Away mode collapses to a single global flag. The two-tier `global_away + cwd_override` model retires.
- **Persistence layer** (T-001). All Conversation state is in-memory; server restart loses in-flight conversations and forces blocked agents to time out. Matches today's "never restart during collab" operational rule. T-001 stays in the backlog as the eventual fix.
- **Old Firebase data migration.** Switchboard's stored state is operational, not user data; the deploy clears `channels/` and starts fresh under `conversations/`.

## Design

### Conversation model

A `Conversation` is the new first-class persistence + routing object. Identity is a server-minted UUID4 string. The conversation carries title, lifecycle state, member rosters, message log, pending question / response slots, and display metadata (hidden flag, preview, unread count, last-activity timestamp).

Three lifecycle states:

- **Open** — accepts new members via `enter_conversation`. Born only on the collab path.
- **Closed** — has at least one active member, accepts no new members. Born on the non-collab path (auto-created when an agent's channel sends its first `xxx_human` while not in any conversation).
- **Ended** — terminal. No active members. Persists in Firebase as history.

Conversations carry a `collab: bool` attribute fixed at creation:

- `collab=true` → born Open, transitions directly to Ended (no Open → Closed mid-life). Multi-member capable.
- `collab=false` → born Closed, transitions to Ended. Always single-member; the channel's identity is implicit.

**Global constraint:** at most one Open conversation exists in the system at any time. Closed conversations are unconstrained.

**Channel ↔ conversation invariant:** a canonical cwd ("channel") is in at most one active (Open or Closed) conversation at a time. The `channel_to_conversation_id` lookup map (server in-memory) is the single source of truth for routing; every cwd-keyed tool call resolves through it.

#### Creation triggers

1. **`enter_conversation(channel, sender, title)`** by a channel that's not in any conversation:
   - If there's an Open conversation system-wide → caller is added to it. Caller blocks per the FIFO talking-stick rules (see below).
   - If there's no Open conversation → server mints a new Open conversation, caller becomes the seed, blocks waiting for a second member to arrive (timeout: 10 minutes).
2. **First `ask_human` / `notify_human` / `send_document_human`** by a channel that's not in any conversation → server mints a new Closed (non-collab) conversation for that channel. The call routes through it.

#### Termination triggers

- **Last active member leaves** (via `leave_conversation`, via the last-one-left sentinel from `message_and_await_agent`, or via the non-collab single member having their owning channel terminate) → Ended.
- **John force-ends** via the phone → Ended. All queued members' Futures resolve with `__CONVERSATION_ENDED__\n<final notice>` payload.
- **Seed timeout** (no second member arrived within 10 min on a brand-new Open conversation) → seed's `enter_conversation` returns with `__SEED_TIMEOUT__` sentinel; conversation Ends.
- **Non-collab + global away flips off**: the moment global away mode transitions True → False, every Closed non-collab conversation Ends.

### Tool surface

Three new tools, one modified, one removed, plus consolidation of away-mode controls.

#### New: `enter_conversation(channel, sender, title)`

Blocking.

- All three args required.
- If caller's channel is already in any active conversation → returns immediately with error `"channel already in conversation; leave_conversation first."`
- If there's an Open conversation system-wide → caller is appended to its FIFO wait queue; blocks. When the FIFO promotes the caller, returns the **full conversation history** payload (every log entry, chronological). Caller now holds the stick.
- If no Open conversation exists → server mints a new Open conversation (this caller is the seed). Caller is appended to the wait queue, blocks. The seed unblocks when a second agent calls `enter_conversation` for the same conversation (i.e. there's now another member). Seed's payload is also full history (empty log at this point — the seed wakes with no messages but with "you're seeded, your peer just arrived" implicit context via empty history + member roster).
- Seed timeout: 10 minutes (configurable). On timeout, the seed's call returns `__SEED_TIMEOUT__`; conversation Ends; caller's channel is freed.
- `title` is required. On a brand-new conversation, it names the conversation. On subsequent joiner calls, `title` is allowed to update the conversation's title (same convention as today's title-on-every-tool rule).

#### New: `leave_conversation(channel, sender, parting_message)`

Non-blocking on success.

- All three args required.
- If caller's channel is not in any conversation → error `"not in a conversation."`
- If `global_away_mode == True` → error `"cannot leave conversation while in away mode."`
- Otherwise: the `parting_message` is appended to the conversation log as a `type="parting"` message; caller is removed from `members_active` (and marked with `left_at` in `members_history`); the channel is unmapped from `channel_to_conversation_id`. The FIFO-oldest blocked member, if any, wakes with the parting in their payload. If the caller was the last active member, conversation transitions to Ended.
- Returns `"ok. Left conversation <id>."` Agent's turn can end (turn-end hook permits — see below).

#### New: `set_away_mode(value: bool)`

Non-blocking.

- Single value-set tool, no cwd arg.
- Flips the single global `away_mode` flag. Persisted to Firebase under `global_settings/away_mode`.
- On `False → True`: all currently-in-conversation channels stay in their conversations.
- On `True → False`: every Closed non-collab conversation transitions to Ended (their reason for existing — away-mode passive logging — is gone). Channels in collab conversations are unaffected.
- Returns `"ok. away_mode=<value>"`.

#### Modified: `message_and_await_agent(channel, sender, message, title?)`

Same shape as today.

- `message` is required and non-empty (rejected with `"ERROR: message is required"` if missing/empty).
- If caller's channel is not in any conversation → returns immediately with `"ERROR: channel not in any conversation. End your turn."`
- If caller is in a non-collab Closed conversation → returns immediately with `"ERROR: this is a non-collab conversation. Use ask_human / notify_human / send_document_human."`
- If caller is the **only active member** of their conversation → returns immediately with `"__CONVERSATION_EMPTY__\n<parting messages from members who left since caller last spoke, chronological>"`. Caller is auto-removed; conversation transitions to Ended.
- Otherwise: caller's `message` is appended to the log as a `type="agent_msg"` speak event; caller is appended to the FIFO wait queue; the FIFO-oldest blocked agent wakes with their appropriate payload.
- When this caller subsequently wakes, the payload is **every log entry since `my.last_seen_seq` except my own emissions** (= entries where `sender == my_sender`), chronological. That covers peer `agent_msg`, peer-originated `ask`/`notify`/`doc`, John's `response` messages, partings — everything in the log delta minus my own outputs.
- `title` is optional and updates the conversation's title when present.

#### Removed tools

- **`end_collab`** — subsumed by `leave_conversation`.
- **`enter_away_mode(cwd)`** / **`exit_away_mode(cwd)`** — replaced by `set_away_mode(bool)`. Per-cwd overrides retire.

#### Unchanged-shape, routing-aware tools

`ask_human`, `notify_human`, `send_document_human` keep their signatures.

- If caller's channel isn't in any conversation → server auto-creates a non-collab Closed conversation for the channel, then routes the call through it.
- Otherwise the call routes through the caller's current conversation.
- If `global_away_mode == False`: `ask_human` and `notify_human` return immediately with the at-desk-redirect ERROR string (`"ERROR: John is at his desk. Ask this question via the terminal."` for `ask_human`; analogous for `notify_human`). The call **still creates the conversation** if absent, **still appends a message to Firebase** (so John sees it as a passive entry on the phone — the existing rejected-bubble UX), and **still updates conversation metadata** (preview, last_activity). It just doesn't block.
- If `global_away_mode == True`: behavior matches today — `ask_human` blocks for a response (24 h timeout); `notify_human` is fire-and-forget.
- `send_document_human` is fire-and-forget and is **not** at-desk-gated (a file delivery has value regardless; matches today's behavior). It still routes via the conversation.

### Routing & talking-stick state machine

#### Server in-memory state

```python
class Registry:
    _conversations: dict[conversation_id, Conversation]                   # active (Open + Closed)
    _ended_conversation_ids: collections.OrderedDict                      # bounded LRU for in-flight force-end cleanup
    _channel_to_conversation_id: dict[canonical_cwd, conversation_id]     # routing map
    _open_conversation_id: conversation_id | None                         # singleton fast-lookup
    _global_away_mode: bool                                               # Firebase-mirrored

@dataclass
class Conversation:
    id: str                                                               # UUID4
    title: str
    collab: bool
    state: Literal["open", "closed", "ended"]
    members_active: dict[sender, ConversationMember]
    members_history: list[ConversationMember]                             # append-only, includes left members
    messages: list[ConversationMessage]                                   # append-only ordered log
    pending_responses: dict[request_id, PendingAskHuman]
    wait_queue: collections.deque[QueueEntry]                             # FIFO of blocked agents
    created_at: float
    last_activity_at: float
    ended_at: float | None
    hidden: bool
    lock: asyncio.Lock

@dataclass
class ConversationMember:
    channel: str                                                          # canonical cwd
    sender: str
    joined_at: float
    left_at: float | None
    last_seen_seq: int

@dataclass
class QueueEntry:
    member: ConversationMember
    future: asyncio.Future
    waiting_kind: Literal["enter", "msg_and_await"]
    block_position: float                                                 # monotonic, FIFO ordering key

@dataclass
class ConversationMessage:
    seq: int
    msg_id: str                                                           # Firebase-style push id
    sender: str
    type: str                                                             # see "Message types" below
    text: str
    url: str | None
    filename: str | None
    request_id: str | None
    attached_to_msg_id: str | None
    timestamp: str                                                        # ISO-8601
    format: Literal["plain", "markdown"]
    suggestions: list[str] | None
    cancelled: bool
    rejected: bool
    title: str | None                                                     # snapshot at message time
    opened: bool
```

The `Conversation.lock` ensures atomicity across membership changes + log writes + queue manipulation. All mutating tool handlers acquire it before touching conversation state.

#### Channel canonicalization

The existing `server/canonicalization.py:canonicalize_cwd` is the single point of normalization. After commit b889472 it accepts Windows / Git-Bash / POSIX absolute paths and produces an opaque routing key. No further changes needed for this design — different cwds produce different keys, and that's the intended behavior (cross-cwd collab uses explicit conversation join, not implicit path-matching).

#### Talking-stick rules

1. **Single FIFO queue** per conversation (`wait_queue`), ordered by `block_position` (monotonic time the agent blocked).
2. **Stick holder** = the (at-most-one) currently non-blocking member. May be 0 (no stick holder; seed waiting for second, or between speak events transiently) or 1.
3. **`enter_conversation`** appends a `QueueEntry(waiting_kind="enter")` to `wait_queue`. If this is the seed of a brand-new conversation (queue empty, no active members yet besides this caller), the seed blocks waiting for a second member's arrival (10-min timeout). If the conversation already has members (joining an existing Open), the joiner blocks until the FIFO promotes them via a speak event.
4. **`message_and_await_agent`** appends `QueueEntry(waiting_kind="msg_and_await")` after writing the speak message.
5. **`leave_conversation`** is a speak event (writes a parting message) plus self-removal.
6. **Wake** on any speak event: pop the head of `wait_queue` (FIFO-oldest), resolve their Future with the appropriate payload. No eligibility filter — the seed-blocks-until-second rule plus the last-one-left sentinel together guarantee the queue is never empty at a speak event in healthy flow.
7. **Wake payload** depends only on `waiting_kind`:
   - `enter` → **full conversation history** (every `ConversationMessage` in chronological order; empty log = empty list).
   - `msg_and_await` → **every log entry since `my.last_seen_seq` except entries where `sender == my_sender`**, chronological.
8. After wake: waker's `last_seen_seq = len(messages)`, Future cleared, waker becomes stick holder.

#### Walkthrough — 3-agent collab

| t | Event | Wait queue (FIFO) | Stick |
|---|---|---|---|
| 1 | A `enter_conversation(...)` — seeds new conv, blocks waiting for second | `[A:enter]` | — |
| 2 | B `enter_conversation(...)` — second arrives, wakes A (payload: empty history); B blocks | `[B:enter]` | A |
| 3 | A `message_and_await_agent(msg1)` — wakes B (payload: full history `[msg1]`); A blocks | `[A:msg]` | B |
| 4 | B `message_and_await_agent(msg2)` — wakes A (payload: `[msg2]`); B blocks | `[B:msg]` | A |
| 5 | C `enter_conversation(...)` — joins existing Open, blocks (A still has stick) | `[B:msg, C:enter]` | A |
| 6 | A `message_and_await_agent(msg3)` — wakes B (payload: `[msg3]`); A blocks | `[C:enter, A:msg]` | B |
| 7 | B `message_and_await_agent(msg4)` — wakes C (payload: full history `[msg1..msg4]`); B blocks | `[A:msg, B:msg]` | C |
| 8 | C `message_and_await_agent(msg5)` — wakes A (payload: `[msg4, msg5]` — log delta since A's `last_seen_seq=3`, no own-emissions to filter); C blocks | `[B:msg, C:msg]` | A |

#### Edge cases

- **Last-one-left**: when only one active member remains and they call `message_and_await_agent`, the handler returns `__CONVERSATION_EMPTY__\n<parting messages>` immediately, removes the caller from `members_active`, transitions the conversation to Ended, and returns. No queue interaction.
- **Force-end** (John triggers via phone): all `QueueEntry.future`s resolve with `__CONVERSATION_ENDED__\n<final notice>`; `members_active` clears; conversation transitions to Ended. The dispatch loop handling `force_end_conversation_commands/` is idempotent — a command targeting an already-Ended conversation is a no-op (logged for visibility, no error surface).
- **Seed timeout**: seed's Future resolves with `__SEED_TIMEOUT__`; same Ended transition.
- **Member crash / process death**: their tool call times out at the MCP 24-h timeout; if they were in a conversation, the conversation stays "stuck" with a zombie member until either (a) the surviving members force-end or all leave, or (b) the server restarts. This is the existing failure mode for in-memory state — T-001 addresses it.

### Away mode (simplified, global only)

- Single Firebase node: `global_settings/away_mode: bool`. Server caches in-memory.
- `set_away_mode(bool)` is the only mutation path from agents.
- John's phone can also write to `away_mode_commands/` with `{type: "set", value: bool, issued_at}`; existing dispatch loop applies the change (cleanly retired the `enter_cwd` / `exit_cwd` types).
- **Implications of global-only**:
  - `Registry._cwd_overrides` and all related plumbing removed.
  - Per-cwd pill chip on Page B removed.
  - Per-cwd swipe-to-flip gesture on Page A removed.
  - The bulk-respond modal on a per-cwd exit (existed to handle pending questions when flipping off a single cwd) is removed; only the global-exit bulk-respond stays.

### Firebase schema

Hard cutover: `channels/<cwd_key>/...` deleted; `conversations/<id>/...` replaces it. Field-by-field mapping below preserves every existing `ChannelMessage` field; nothing visible on the phone is dropped.

```
conversations/<conversation_id>/
  title                       (str)
  collab                      (bool)
  state                       "open" | "closed" | "ended"
  created_at                  (iso-8601 str)
  last_activity_at            (iso-8601 str)
  ended_at                    (iso-8601 str | null)
  hidden                      (bool)
  preview                     (str — latest message snippet)
  unread_count                (int)
  pending_responses           (int — badge count)

  members_active/<sender>/
    channel                   (canonical cwd)
    sender
    joined_at
    last_seen_seq             (int)

  members_history/<push_id>/
    channel
    sender
    joined_at
    left_at                   (null while active; iso once left)
    parting_msg_id            (str | null — link into messages/)

  messages/<msg_id>/          # every existing ChannelMessage field, verbatim
    sender                    (str)
    type                      "agent_msg" | "ask" | "response" | "notify" | "doc" | "parting" | ...
    text                      (str)
    url                       (str | null)
    filename                  (str | null)
    request_id                (str | null)
    attached_to_msg_id        (str | null)
    timestamp                 (iso-8601)
    format                    "plain" | "markdown"
    suggestions               (list[str] | null)
    cancelled                 (bool)
    rejected                  (bool)
    title                     (str | null — snapshot at message time)
    opened                    (bool)

  pending_questions/<request_id>/
    sender
    questionText
    cancelled
    msgId                     (links to messages/<msg_id>)
    suggestions

  answered_question_msg_ids/<msg_id>/ -> true

  agent_status/               # one record per conversation; written only when stick holder hooks fire
    sender                    (current stick holder)
    state                     "thinking" | "tool:<name>"
    detail
    updated_at                (epoch ms)

global_settings/
  away_mode                   (bool — single source of truth)

away_mode_commands/<push_id>/
  type                        "set"
  value                       (bool)
  issued_at

force_end_conversation_commands/<push_id>/    # NEW
  conversation_id
  issued_at

spawn_commands/...            # node retained for server-side compatibility; no client writes
inject_queue/<conversation_id>/...            # was keyed by cwd_key; now keyed by conversation_id
```

**Message type values** (`messages/<msg_id>/type`):

- `agent_msg` — `message_and_await_agent` speak event
- `ask` — `ask_human` question (whether blocked or at-desk-redirected)
- `response` — John's reply to an `ask`
- `notify` — `notify_human` emission (whether delivered or at-desk-redirected)
- `doc` — `send_document_human` delivery
- `parting` — `leave_conversation` message (new)

**Removed Firebase nodes**:
- `channels/<cwd_key>/...` (entire subtree)
- `away_mode_commands` of types `enter_cwd` / `exit_cwd` (only `set` remains)
- Any per-cwd away-mode override storage

### Android UI

- **Page A (conversations list)** — replaces channel list. One row per conversation (active + recently ended). Row content: title, preview, last-activity, unread/pending badge. Long-press opens the existing context menu (now offering Hide / Unhide + End conversation when applicable). "Show hidden" toggle in the overflow menu retained.
- **Page B (conversation view)** — replaces channel view. Title bar shows the conversation title plus a sub-line listing active members when membership > 1 (e.g. `Claude (C:\Work\Switchboard) + Gemini (/home/john/work/switchboard)`). When membership == 1 (non-collab), no sub-line. Bubble feed renders messages in chronological order with per-sender attribution (multi-member collab needs each speaker tagged on their bubble). Reply input visible when a pending `ask_human` exists in the conversation, attributed to the asking agent.
- **Page A row swipe gestures** — both gestures use `SwipeToDismissBox`, both snap back, both raise a confirmation dialog before mutating:
  - **Swipe right (left-to-right, `SwipeToDismissBoxValue.StartToEnd`) → end conversation.** Only enabled when `conversation.collab == true AND conversation.state == "open"` (non-collab, Ended, or already-Closed rows: gesture snaps back with no action). Confirmation dialog: `"End conversation '<title>'? All members will be notified and removed."`. On confirm → phone writes a `force_end_conversation_commands/<push_id>/{conversation_id, issued_at}` record; server-side dispatch loop ends the conversation. Background color: red with end-call icon. Replaces today's StartToEnd swipe (which flipped the channel to "At desk" via `onExitAway` and retires alongside per-channel away mode).
  - **Swipe left (right-to-left, `SwipeToDismissBoxValue.EndToStart`) → hide.** Enabled for any row. Confirmation dialog: `"Hide '<title>'? It will be accessible via 'Show hidden' in the overflow menu."`. On confirm → phone writes `hidden=true` to the conversation. Background color: amber/grey with hide icon. Direction matches today's swipe-to-hide (`SessionRowComposable.kt:72-75`); the change is adding the confirmation step that the current implementation lacks.
- **No per-conversation away pill anywhere.** Only the global pill chip on Page A app-bar remains, and it writes a `set` command to `away_mode_commands/`.
- **Agent status**: one row per conversation; binds to `conversations/<id>/agent_status/`. Renders the current stick-holder's state.
- **Spawn UI removed**: spawn FAB on Page A, spawn dialog, spawn collision dialog — all deleted. Backlog item T-027 covers the redesigned spawn.
- **Bulk-respond modal**: only the global-exit variant remains. Per-channel exit variant retired.

### Hooks

- **Turn-end hook** (`turn-end-hook-away-mode.py` — name reflects history; functionally now covers two block conditions):
  - Blocks turn-end if **`global_away_mode == True`**, OR
  - Blocks turn-end if the agent's channel is currently in any conversation (Open or Closed). Block message: `"you're in conversation <id>; call leave_conversation first (and address any pending message_and_await_agent or leave_conversation rules)."`
  - The existing redirect prompt nudging agents to `ask_human` fires only for the away-mode branch.
  - Server endpoint extended (or new) to return both flags + the agent's current conversation_id (if any).
- **Agent-status hook** (`agent-status-hook.py`):
  - Writes to `conversations/<id>/agent_status/` after server-side conversation lookup.
  - Server gates the write: only the stick holder's status update lands; non-stick-holder writes are no-ops. Quiet-when-at-desk gate (existing) continues to apply.

### SKILL.md

Major rewrite. Sections that change:

- **CRITICAL: Away Mode Protocol**: rewrites around single global flag + `set_away_mode(bool)`. "User-managed flag" rule preserved — agents only flip on explicit signal in the most recent prompt. "Stepping away" trigger calls `set_away_mode(True)`. "Back at desk" calls `set_away_mode(False)`.
- **Switchboard MCP Tools** tool list:
  - Add `enter_conversation(channel, sender, title)`, `leave_conversation(channel, sender, parting_message)`, `set_away_mode(value)`.
  - Remove `end_collab`, `enter_away_mode`, `exit_away_mode`.
  - Update `message_and_await_agent` description (errors if not in conversation; message required; payload semantics).
- **Conversations section (new, sizeable)**: the conversation model, lifecycle, talking-stick FIFO, seed-blocks-until-second, payload semantics, leave-blocked-in-away-mode rule, the various sentinel returns (`__CONVERSATION_EMPTY__`, `__CONVERSATION_ENDED__`, `__SEED_TIMEOUT__`).
- **Collab section**: rewritten around `enter_conversation` / `leave_conversation`. BYO-collab via parallel msg_and_await retires entirely.
- **Naming caveat for same-type BYO pairs**: still applies (sender uniqueness within a conversation).
- **`/spawn --collab` flow** description: removed (spawn UI gone from client; the section can be deferred until T-027).

### Spawn (deferred)

Server-side `server/spawn.py`, `server/gateway/handlers.py` spawn closures, the `SwitchboardSpawn` scheduled task, and `scripts/spawn-launcher.ps1` all **stay intact**. Touch only what's needed for compile correctness against the new conversation model — for example, spawn-collision logic that previously read `channels/<cwd_key>/...` for "is there residual state at this cwd?" now reads against the empty `channels/` subtree (gracefully returns "no collision") or, where the code defensively unwraps fields that no longer exist, gets a small null-guard. Behavior is preserved as a working no-op pending the T-027 redesign; no existing logic deleted. The phone's removal of the spawn entry point means `spawn_commands` writes don't happen in practice, but server-side dispatchers remain wired up.

Backlog entry T-027: **"Bring back spawn with conversation-aware redesign"** — covers spawn UI, session-resume semantics, and how spawn interacts with conversation auto-creation rules.

### Migration

Hard cutover. On first deploy:

1. Server startup wipes the `channels/` subtree from Firebase (one-time idempotent delete).
2. New `conversations/` subtree begins empty.
3. All Android clients require a fresh install / app-data-clear to drop stale local cache from the old schema.

No legacy data preservation. Existing logs in `logs/switchboard.jsonl` remain intact as historical reference.

## Testing strategy

In-process integration tests (matching the existing `tests/` style) cover:

- **Lifecycle**: Open creation via seed + second-enter, Closed creation via first `xxx_human`, Ended via last-leave, Ended via force-end, Ended via non-collab away-flip-off, Ended via seed timeout.
- **Talking-stick FIFO**: 2-agent ping-pong, 3-agent rotation with mid-stream joiner, last-one-left sentinel, cross-member ask_human visibility (peer wakes with question + response in payload).
- **Routing**: cwd → conversation lookup, multiple cwds in one collab, switching from non-collab Closed to a new conversation after channel exits its previous one, channel-already-in-conversation rejection on `enter_conversation`.
- **Away mode**: global flag transitions, non-collab teardown on True→False, at-desk-redirect on `ask_human`/`notify_human` while creating + logging the conversation entry.
- **Tool errors**: empty `message` rejection, `message_and_await_agent` from a non-collab conversation, `leave_conversation` in away mode, `enter_conversation` from a channel already in a conversation.

Firebase backend is mocked, per existing `tests/` patterns.

## Feature & UX preservation audit

Exhaustive walk through the existing implementation to confirm what survives, what changes, and what is deliberately retired. Anything material not on these lists is either covered earlier or unaffected.

### Preserved verbatim (no UX or behavior change)

- **Markdown rendering** of message bodies (`format: "plain" | "markdown"` field carries through).
- **Suggestion buttons** on `ask_human` (tap-to-respond, fallback to free-text reply). Existing button parsing + reply path unchanged; the `suggestions` array survives on `ConversationMessage`.
- **Document attachments via `send_document_human`** — `server/gateway/document.py`'s validation (denylist pattern matches `.env*`, `*token*`, `*secret*`, `*.pem`, `*.key`; 5 MB size cap; SHA-256 logging) is unchanged. Documents continue to land under conversation `messages/` with `type="doc"`, `filename`, `url`, `opened` fields.
- **Per-bubble pinch-to-resize text** (T-010) — orthogonal Compose modifier, unaffected by routing changes.
- **Pull-to-reveal-timestamps** gesture on the message list — orthogonal.
- **Voice dictation for watch replies** + **watch notifications** — wear surface unchanged by this redesign.
- **MarkdownText subclass workaround** for the AOSP drag-shadow bug (T-138) — unchanged Compose factory.
- **Three FCM notification channels** ("three-way notification channel split" per AGENTS.md — Asks / Updates / Documents). The FCM `notification_channel_id` selection logic stays; payloads now carry `conversation_id` instead of `channel_id` for deep-linking.
- **FCM tap deep-linking** — taps still open Page B for the referenced conversation (was: cwd-keyed channel; now: conversation_id-keyed). Same intent structure on the Android side.
- **JSONL audit log** (`logs/switchboard.jsonl`) — every tool call still logs there; correlation fields adapt from `cwd` to `(conversation_id, cwd, sender)`.
- **Stale reply handling** — phone replies arriving for an unknown correlation tuple still log + emit a "stale reply" notice on the conversation and delete the `responses/` slot. `dispatch_responses` adapts to look up by `(conversation_id, sender)` instead of `(cwd, sender)`.
- **Listener supervision + `/healthz`** (per `2026-05-01-listener-supervision-and-healthz-design.md`) — `SupervisedListener` + `LoopSupervisor` patterns continue to wrap every Firebase listener; listener paths move from `channels/<key>/...` to `conversations/<id>/...` and `force_end_conversation_commands/`. `/healthz` adds new per-loop entries for the conversation-route listeners + the new force-end dispatch loop.
- **Title behavior on every messaging tool** — `title` remains optional on every tool. First call sets, subsequent calls update on material scope shift. The "noun phrase / verb-ing form, ≤80 chars, no trailing punctuation" convention stays in SKILL.md.
- **Partner-title-change relay** (`title_tracker.maybe_prepend`) — when a peer's session title changes between speak events, their next message is prefixed with `[<peer>'s current session title: "<title>"]\n\n<message>` for context. This carries through to conversations; the prepend fires on partner-title-change for each receiving member.
- **`ask_human` supersede semantics** — if a new `ask_human` arrives for `(conversation_id, sender)` while one is pending, the prior future is cancelled and the prior question's Firebase entry is marked `cancelled=true`. Today this is keyed by `(cwd, sender)` — same logic, new key.
- **MCP stateful HTTP transport** + **cancellation propagation** — `stateless_http=False` preserved; `notifications/cancelled` from Claude Code continues to mark in-flight `ask_human` and `message_and_await_agent` questions `cancelled=true`. The Gemini CLI's lack of cancel propagation is a known limitation that doesn't worsen here.
- **24-hour MCP timeout** — unchanged.
- **Spawn flow** (server side only) — `server/spawn.py`, `server/gateway/handlers.py` spawn closures, `SwitchboardSpawn` scheduled task, `scripts/spawn-launcher.ps1`, `quser` no-login gate (post-2026-05-02) all stay intact. The phone simply cannot trigger them while the client UI is gone (T-027 redesigns).
- **At-desk message-still-creates-channel-and-logs** behavior for `ask_human` redirect (T-023) — extends to `notify_human` in this design (both gated), `send_document_human` continues to deliver regardless of away-mode state.
- **Rate limiter** for `notify_human` + `send_document_human` — `RateLimiter`'s shape is unchanged; the key changes from `canonical_cwd` to `conversation_id` so the bucket is scoped to "this conversation" rather than "this channel." If we want per-sender granularity later, the key generalizes naturally (`f"{conversation_id}:{sender}"`); not done in v1 since the channel-level bucket suffices.
- **Bulk-respond modal** — global-scope variant survives (the dialog the phone shows when flipping global away → at-desk while pending questions exist). Logic in `_apply_bulk_respond_decision` keeps its `send_default` / `skip` / `cancel` decision shape; `scope_cwd` parameter retires (always None / global).
- **Inject queue** — `dispatch_inject_queue` continues to feed human-injected messages from the phone compose box into active conversations. Keyed by `conversation_id` instead of `cwd`. Phone composes by writing to `inject_queue/<conversation_id>/<inject_id>/{text, issued_at}`.
- **Agent-status hook + pulsing channel-list dot + inline status row** (T-139) — preserved at the conversation level. Hook writes filter to the stick-holder only; phone shows one status indicator per conversation.
- **`logs/sessions/<channel_key>.log`** per-channel session log — repurposed to `logs/sessions/<conversation_id>.log`; `_append_session_log` helper retargets accordingly.
- **`canonicalize_cwd`** logic (post-b889472) — unchanged. Two-cwd collab works because cwd is now just member identity; the conversation ID is the routing key.
- **`PendingRequest.msg_id` linkage** — links a question's Firebase msg_id to the pending entry so replies can set `attached_to_msg_id` for in-line reply rendering. Preserved verbatim, now scoped under the conversation.

### Deliberately retired (with replacement)

- **`CollabSession` class** (`server/collab.py`) — replaced by `Conversation`. The pre-enroll buffer, deadlock guard, message coalescing (H10), partner-title-change buffering all collapse into the Conversation's append-only log + FIFO wait queue.
  - The H10 coalescing concern ("backlog of N buffered messages from partner delivers FIFO across N successive `start_waiting` calls — recipient replies to message k while k+1 is still queued") is structurally precluded by the new payload semantic (every wake delivers the full delta since last_seen_seq, not one message at a time).
- **Registry surfaces tied to old model** — retired: `_sessions`, `_cwd_overrides`, `_recently_ended` (the simultaneous-end_collab race that motivated this can't occur under FIFO talking-stick — only the stick holder can call `leave_conversation`), `_last_messaging_sender`, `is_away_mode_active(cwd)`, `set_cwd_override`, `remove_cwd_override`, `update_cwd_override_cache`, `cwd_overrides()`, `record_messaging_sender`, `last_messaging_sender_for`, `get_collab_baton_holder` (stick holder is the explicit state now).
- **`dispatch_away_mode_commands` command types** — `enter_global` / `exit_global` / `enter_cwd` / `exit_cwd` retire; replaced by a single `set` type (value: bool). `_clear_all_cwd_overrides` retires entirely.
- **`bulk_respond.py` `scope_cwd` parameter** — retired; only global scope remains.
- **`end_collab` tool + reporter handoff logic** — retired; `leave_conversation` covers single-agent exit, last-one-left handling, and the empty-conversation cleanup.
- **BYO implicit enrollment** (auto-pairing of two same-cwd agents via parallel `message_and_await_agent` calls) — retired; collab requires explicit `enter_conversation`.
- **Per-cwd away-mode UI** — pill chip on Page B (long-press toggle), the swipe-to-flip-at-desk gesture, the per-channel bulk-respond dialog — all retired.
- **Spawn dialog UI** — removed from Android (FAB, dialog, collision dialog). Server-side spawn handler stays as a no-op pending T-027.
- **`enter_away_mode(cwd)` / `exit_away_mode(cwd)`** tools — retired; collapsed into `set_away_mode(bool)`.

### Newly added (no prior equivalent)

- **`force_end_conversation_commands` Firebase node** + dispatch loop on the server. Phone writes a record when the swipe-to-end gesture is confirmed; server pops it, looks up the conversation, force-ends (resolves every queued Future with `__CONVERSATION_ENDED__\n<final notice>`, clears `members_active`, transitions state).
- **Confirmation dialog on swipe-to-hide** — today the gesture mutates immediately; the spec adds the confirmation step.

### Known regressions, accepted

- **Spawn from the phone is gone** until T-027 lands. Today's `/spawn` and `/spawn --collab` flows are the primary way John starts agents from his phone. Until T-027, agents are launched locally (terminal, Claude Code session, etc.) and can join a conversation via `enter_conversation`.
- **Existing conversation history is wiped** on cutover. No migration of `channels/<cwd_key>/messages/...` into `conversations/<id>/messages/...`. Stored history pre-cutover lives only in `logs/switchboard.jsonl` and per-channel session logs.
- **Server crash during an Open collab loses queue state** (matches today's constraint). Blocked agents time out at the 24h MCP timeout; the Conversation is left "stuck" in Firebase as Open with phantom members until force-ended or persistence (T-001) ships.

## Open questions / future work

- **Spawn redesign** (T-027) — separate brainstorm. Especially how spawn interacts with "resume last session" (overlap with existing T-016).
- **Cross-host A2A** (T-025) — remains low priority. The conversation abstraction is the natural extension point.
- **Persistence layer** (T-001) — would convert the "never restart during collab" operational rule into a soft-loss. Out of scope here but enabled by this design.
- **Garbage collection** (T-003) — conversation cleanup for stuck Open conversations (zombie members). Worth picking up after this lands.
- **Per-sender rate limiting within a conversation** — v1 scopes the rate-limit bucket to `conversation_id`; if abuse patterns surface where one member burns the whole bucket and starves a peer, regrade to `(conversation_id, sender)` keys.

## Supersedes / relates to

- **Supersedes**: the implicit BYO-collab pattern in [`2026-04-23-bring-your-own-session-design.md`](2026-04-23-bring-your-own-session-design.md); the per-cwd away-mode override path in [`2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md`](2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md); the Firebase schema in [`2026-04-28-away-mode-firebase-schema-reorg-design.md`](2026-04-28-away-mode-firebase-schema-reorg-design.md) (replaced by the conversations subtree).
- **Relates to**: T-025 (cross-host A2A; this design is the same-host narrow case), T-026 (cwd canonicalization gap; resolved at the canonicalization layer in b889472, generalized here at the routing layer), T-001 (persistence; future fix for the in-memory loss this design accepts), T-027 (new; spawn redesign), T-028 (narrower alternative: surgical FIFO talking-stick refactor inside the existing `CollabSession` — captures the protocol-cleanup wins of this design without the schema migration, Android Page A/B rewrite, or Spawn UI loss; leaves cross-cwd collab and away-mode unification unaddressed).
