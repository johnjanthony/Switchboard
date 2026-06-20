# Conversations: Collab Redesign — Design

**Date:** 2026-05-19
**Branch context:** `develop` (post-canonicalize-cwd-POSIX commit b889472)
**Status:** implemented in commit `c44b632` on 2026-05-26 (branch `session_id-as-key`); paired with the T-027 spawn redesign. Divergences from this design are listed in the **Implementation deltas** section near the end of this document.

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

Two lifecycle states:

- **Active** — has at least one member (alive or dormant). Accepts new members via server-side mechanisms (spawn-into-existing, combine, resume) and via the agent-driven `enter_conversation()` tool when the conversation is the singleton openConversation. Multi-member capable from a single member upward; no creation-time distinction between solo and collab.
- **Ended** — terminal. No active or dormant members. Persists in Firebase as history.

**No `collab: bool` attribute.** Multi-member status is a runtime fact based on `len(members_active)`, not a creation-time decision.

**`openConversationId: conversation_id | None` — server-side global pointer.** At most one Active conversation is "the open one" (agent-joinable via `enter_conversation()`) at any time. The pointer is set/replaced by the `open_conversation()` MCP tool and cleared automatically when the referenced conversation transitions to Ended or another `open_conversation()` call replaces it.

**Channel ↔ conversation invariant (session-id-based):** a CLI session (`cli_session_id`) is in at most one Active conversation at a time. The `_session_to_conversation_id` lookup map (server in-memory, persisted to Firebase) is the single source of truth for routing; every switchboard MCP call resolves through it via the hook-injected `cli_session_id`. The agent-supplied `cwd` is informational (display) only — never used for routing.

**Member states (`alive | dormant | permanently lost`):**

- **Alive** — `cli_session_id` is currently bound in `_session_to_conversation_id`; the agent is running and responsive.
- **Dormant** — `cli_session_id` is captured on the member entry but not currently bound; the CLI session has exited (SessionEnd hook fired with reason `logout`/`prompt_input_exit`/`other`); the member is retained in `members_active` for revival via resume or combine.
- **Permanently lost** — the underlying CLI session is unrecoverable (`clear`/`compact` SessionEnd reason set `session_lost_permanently = True`). Member entry is retained for visibility but can't be revived.

**Session-fallback rule (member-removal paths).** When a session is removed from a conversation (via `leave_conversation`, force-end, or any path other than combine — which moves them to target instead of removing), the session is NOT orphaned:

- **If `global_away_mode == True`**: the session is re-bound to its **home conversation**. Each session has a `home_conversation_id` set at first switchboard contact (typically the spawn-pre-bound conversation, or the auto-created Active conversation from a first `xxx_human` call). If the home is still Active, the session re-joins it; if the home is Ended, the server creates a new Active conversation and updates the session's home pointer.
- **If `global_away_mode == False`**: the session is unbound. Subsequent `ask_human` / `notify_human` calls at-desk-redirect; the agent's output reaches John via the terminal. If away later flips on, the next switchboard MCP call auto-creates a new Active conversation per the "first xxx_human from an unbound session" rule.

Combine is exempted from session-fallback: it moves members to the target conversation directly.

#### Creation triggers

1. **First `ask_human` / `notify_human` / `send_document_human`** by a session that's not bound to any conversation → server mints a new Active conversation; member added (alive) with the hook-injected `cli_session_id` and `cwd` plus the agent-supplied `sender`; session's `home_conversation_id` set to this conversation if not already set.
2. **`enter_conversation(sender)`** by an unbound session, when `openConversationId` is set → session is added to the openConversation as a new member.
3. **Server-side pre-binding via spawn-into-existing or combine** → server pre-binds `_session_to_conversation_id[cli_session_id] = target_id`; member entry created on the agent's first MCP call (the member-add-on-first-call rule).
4. **Resume** mints a new Active conversation with `continued_from: <source_id>` (see T-027 for full resume mechanics).

#### Termination triggers

- **Last alive member explicitly `leave_conversation`s AND no dormant members remain** → Ended. The leaving session falls back per the session-fallback rule.
- **John force-ends** via Page A long-press → Ended; every alive/dormant member falls back per the session-fallback rule (members are never orphaned by force-end).
- **`combine_conversations(source_id, target_id)`** — source Ends as part of the combine. Members move to target; combine has its own member-migration semantics.
- **Resume of a source where all members were resumable** — source Ends as part of resume.

The parent design's earlier "seed timeout" and "Non-collab + global away-flip-off ending all Closed conversations" triggers retire (no Closed state to apply them to; no seed-blocks-until-second mechanic).

### Tool surface

Two structural changes across all switchboard MCP tools:

1. **Drop the agent-supplied `channel` parameter.** The agent never passes cwd or any cwd-derived key. Routing is by `cli_session_id`, injected by the PreToolUse hook.
2. **Add hook-injected `cli_session_id` (required) and `cwd` (required) on every tool.** Hook-injected = the agent doesn't touch them; the `cli-session-injector-hook.py` PreToolUse hook reads them from its hook-event input JSON and merges them into the tool call's `updatedInput`. Calls missing `cli_session_id` are rejected at the MCP boundary.

Plus three new tools, two modified, and the existing tool surface kept where unchanged. Sender is agent-supplied (a display label; no uniqueness enforced; the spawn prompt template encourages a distinct name with optional guidance from John).

#### New: `enter_conversation(sender, cli_session_id, cwd)`

Unified "join + listen for intro" tool. Blocking.

- `sender` is the agent's declared display name (required, since the call may add a new member to a conversation).
- Behavior branches on caller's current state:
  - **Caller's `cli_session_id` is bound to a conversation X** (typical post-combine / spawn-into-existing case): caller is already in X. The `sender` parameter may update the member's display name. Tool queues caller in X's wait queue without writing a speak event; blocks until next peer speak. Used by newly-arrived members to receive an intro.
  - **Caller's `cli_session_id` is NOT bound to any conversation** AND `openConversationId` exists: caller is added to the openConversation as a new member with the supplied `sender`; queued in its wait queue; blocks for intro.
  - **Caller's `cli_session_id` is bound to conversation X AND X ≠ openConversationId AND openConversationId exists**: caller migrates from X to the openConversation (removed from X per the session-fallback rule's removal path, but re-bound to openConversation rather than home); caller added to openConversation with the supplied `sender`; queued in its wait queue; blocks for intro.
  - **`openConversationId` is null AND caller is not already in a conversation**: error `"ERROR: no open conversation. Ask John to open one on the phone, or have an agent already in a conversation call open_conversation."`.
  - **`openConversationId` is null AND caller IS in a conversation**: just queue in that conversation's wait queue; no migration.
- When the FIFO promotes the caller, returns the **delta of conversation log since caller's `last_seen_seq`** (or **full history** for the new-member-join branches).

#### New: `open_conversation(sender, title?, cli_session_id, cwd)`

Non-blocking.

- Promotes the caller's current conversation to be `openConversationId`. Replaces any prior open marker. If caller isn't in any conversation, errors with `"no current conversation to open"`.
- Optional `title` updates the conversation's title. The `sender` parameter may update the member's display name.
- Used in the prompt mechanic: John tells agent A "start a new collab session" → A calls `open_conversation(...)` → A's conversation is the open one → other agents told "join the open collab" call `enter_conversation(...)` and migrate in.

#### New: `combine_conversations(source_id, target_id, cli_session_id, cwd)`

Non-blocking.

- Move all members of `source_id` into `target_id`. `source_id` ends.
- Alive members rewire via `_session_to_conversation_id`. Dormant members are auto-resumed (launcher fires `claude --resume <session_id>` per-member). Permanently-lost members stay in source's `members_active` for visibility.
- Available to agents (so they can compose collabs from terminal dialogue with John) AND from the phone (long-press → "Combine into…" picker → confirm). See [`2026-05-20-spawn-conversation-aware-redesign-design.md`](2026-05-20-spawn-conversation-aware-redesign-design.md) for the full combine flow.

#### New: `lookup_conversation_ids(cwd?, sender_contains?, title_contains?, cli_session_id, cwd)`

Non-blocking; returns a list.

- At least one of `cwd`, `sender_contains`, `title_contains` required; multiple filters AND together.
- Returns matching conversation_ids. Lets agents resolve concrete `conversation_id`s when calling `combine_conversations` (e.g., "find the conversation titled 'switchboard plugin work'").

#### Modified: `leave_conversation(sender, parting_message, cli_session_id, cwd)`

Non-blocking.

- `parting_message` is required.
- If caller's `cli_session_id` is not in any conversation → error `"not in a conversation."`
- **No "cannot leave while in away mode" guard.** Members are never orphaned: the session-fallback rule routes the leaving session back to its home conversation (away on) or to unbound terminal output (away off).
- Otherwise: `parting_message` appended to the conversation log as a `type="parting"` message; caller removed from `members_active`; entry added to `members_history` with `left_at = now()`. The FIFO-oldest blocked member wakes with the parting in their payload. If the caller was the last alive member AND no dormant members remain, conversation transitions to Ended. Session falls back per the session-fallback rule.

#### Modified: `message_and_await_agent(sender, message, ..., cli_session_id, cwd, title?)`

- `message` is required and non-empty (rejected with `"ERROR: message is required. The 'listen without speaking' use case is enter_conversation()."` if missing/empty). The "listen without speaking" use case is served by `enter_conversation()` instead.
- If caller's `cli_session_id` is not in any conversation → returns immediately with `"ERROR: not in any conversation. End your turn."`
- If caller is the **only active member** of their conversation → returns immediately with `"__CONVERSATION_EMPTY__\n<parting messages from members who left since caller last spoke, chronological>"`. Caller is removed per the session-fallback rule; conversation transitions to Ended.
- Otherwise: caller's `message` is appended to the log as a `type="agent_msg"` speak event; caller is appended to the FIFO wait queue; the FIFO-oldest blocked agent wakes with their appropriate payload.
- When this caller subsequently wakes, the payload is **every log entry since `my.last_seen_seq` except my own emissions** (entries where `sender == my_sender`), chronological.
- `title` is optional and updates the conversation's title when present.

#### `set_away_mode(value, cli_session_id, cwd)`

Non-blocking.

- Single value-set tool. Flips the global `away_mode` flag. Persisted to Firebase under `global_settings/away_mode`.
- Spawn dispatch auto-enables this when False (T-027) — agents rarely need to call it explicitly except for the "I'm back at desk" transition.

#### Removed tools

- **`end_collab`** — subsumed by `leave_conversation`.
- **`enter_away_mode(cwd)`** / **`exit_away_mode(cwd)`** — replaced by `set_away_mode(bool)`.

#### `ask_human`, `notify_human`, `send_document_human` (routing-aware, signatures gain hook-injected params)

- If caller's `cli_session_id` isn't in any conversation → server auto-creates a new Active conversation (one member: the caller); session's `home_conversation_id` set if not already; the call routes through the new conversation.
- Otherwise the call routes through the caller's current conversation.
- Away-mode gating (unchanged from before):
  - If `global_away_mode == False`: `ask_human` and `notify_human` return immediately with the at-desk-redirect ERROR string. The call **still creates the conversation** if absent, **still appends a message to Firebase**, and **still updates conversation metadata**. It just doesn't block.
  - If `global_away_mode == True`: `ask_human` blocks for a response (24h timeout); `notify_human` is fire-and-forget.
- `send_document_human` is fire-and-forget and is **not** at-desk-gated.

### Routing & talking-stick state machine

#### Server in-memory state

```python
class Registry:
    _conversations: dict[conversation_id, Conversation]                   # active conversations
    _ended_conversation_ids: collections.OrderedDict                      # bounded LRU for in-flight force-end cleanup
    _session_to_conversation_id: dict[cli_session_id, conversation_id]    # routing map (replaces _channel_to_conversation_id)
    _session_home_conversation_id: dict[cli_session_id, conversation_id]  # per-session fallback target for the session-fallback rule
    _open_conversation_id: conversation_id | None                         # the singleton agent-joinable conversation pointer
    _global_away_mode: bool                                               # Firebase-mirrored

@dataclass
class Conversation:
    id: str                                                               # UUID4
    title: str
    state: Literal["active", "ended"]
    members_active: dict[sender, ConversationMember]
    members_history: list[ConversationMember]                             # append-only; explicit-leave + force-end departures only (NOT dormancy)
    messages: list[ConversationMessage]                                   # append-only ordered log
    pending_responses: dict[request_id, PendingAskHuman]
    wait_queue: collections.deque[QueueEntry]                             # FIFO of blocked agents
    continued_from: conversation_id | None                                # set on resume-spawn; references prior conversation
    created_at: float
    last_activity_at: float
    ended_at: float | None
    hidden: bool
    lock: asyncio.Lock

@dataclass
class ConversationMember:
    cli_session_id: str                                                   # primary key; replaces `channel`
    sender: str                                                           # display name, agent-supplied; not uniqueness-enforced
    cwd: str                                                              # informational (display only); not used for routing
    surface: Literal["windows", "wsl"]                                    # which surface the member's CLI runs on
    alive: bool                                                           # True if cli_session_id is currently bound; False if dormant
    session_lost_permanently: bool                                        # True if SessionEnd reason was clear/compact (unrecoverable)
    session_ended_at: str | None                                          # ISO-8601 when alive flipped False
    session_end_reason: str | None                                        # SessionEnd hook reason
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

The `Conversation.lock` ensures atomicity across membership changes + log writes + queue manipulation. All mutating tool handlers acquire it before touching conversation state. Combine acquires both source and target locks in `(min, max)` order to avoid AB-BA deadlock.

#### Cwd canonicalization (display-only)

The existing `server/canonicalization.py:canonicalize_cwd` is **no longer load-bearing for routing.** Routing is by `cli_session_id`. Canonicalization continues to normalize cwd strings for display (so Page B shows a consistent cwd label regardless of how the agent passed it: Windows / Git-Bash / POSIX / WSL `/mnt/<letter>/...` all map to a stable display form). Cross-cwd collab works because cwd is no longer involved in routing decisions.

T-026's remaining sub-cases (cross-clone WSL, cross-host) are no longer routing concerns; they remain open only for the optional display-side question of whether a Linux-native WSL path should map to a Windows-equivalent display when the user expects "same project, different surface."

#### Talking-stick rules

1. **Single FIFO queue** per conversation (`wait_queue`), ordered by `block_position` (monotonic time the agent blocked).
2. **Stick holder** = the (at-most-one) currently non-blocking alive member. May be 0 (no stick holder; conversation has no alive members, only dormant; or between speak events transiently) or 1.
3. **`enter_conversation`** appends a `QueueEntry(waiting_kind="enter")` to `wait_queue` per its branching behavior (see Tool surface above). The caller may already be a member (post-combine / spawn-into-existing intro case), may be newly added to openConversation, or may be migrated from another conversation. In all cases the queue entry blocks until the FIFO promotes the caller via a speak event.
4. **`message_and_await_agent`** appends `QueueEntry(waiting_kind="msg_and_await")` after writing the speak message.
5. **`leave_conversation`** is a speak event (writes a parting message) plus self-removal (per the session-fallback rule, the leaving session is re-bound to its home or unbound, not orphaned).
6. **Wake** on any speak event: pop the head of `wait_queue` (FIFO-oldest), resolve their Future with the appropriate payload. The queue may be empty (e.g., a conversation with only the speaker alive, all peers dormant) — speak events with no waiters simply append to the log; the dormant members will see the message via their wake payload when (and if) they're revived via resume/combine. **No seed-blocks-until-second mechanic** — the parent design's prior version relied on it for Open conversations; the new model uses explicit `open_conversation()` + `enter_conversation()` flows and doesn't have a "first to arrive must wait for a second" gate.
7. **Wake payload** depends only on `waiting_kind`:
   - `enter` → if caller was newly added to the conversation, **full conversation history** (every `ConversationMessage` in chronological order). If caller was already a member (intro-receive case), **delta since `caller.last_seen_seq`** (so they don't re-read content they've already seen).
   - `msg_and_await` → **every log entry since `caller.last_seen_seq` except entries where `sender == caller.sender`**, chronological.
8. After wake: waker's `last_seen_seq = len(messages)`, Future cleared, waker becomes stick holder.

#### Walkthrough — 3-agent collab via open_conversation + enter_conversation

Assumes: A spawned solo first (lands in their own Active conversation X with home_conversation_id = X). B and C are subsequently spawned solo (each in their own home). John tells A to open for collab, then tells B and C to join.

<!-- markdownlint-disable MD060 -->
<!-- Walkthrough table cells are intentionally long-form prose; aligned pipes
     would force unreadable column widths. Accepted as-is. -->
| t | Event | Wait queue (FIFO) | Stick |
|---|---|---|---|
| 1 | A `open_conversation(sender="Claude-A", title="bug investigation")` — A's conversation X becomes openConversationId; A is sole member; non-blocking | `[]` | A |
| 2 | B `enter_conversation(sender="Claude-B")` — B migrates from their home Y to X; B added to X's `members_active`; B's `last_seen_seq = 0`; B queued in `wait_queue`; Y ends (B was sole member) | `[B:enter]` | A |
| 3 | A `message_and_await_agent(msg1)` — wakes B (payload: full history `[msg1]`, since B is newly added); A blocks | `[A:msg]` | B |
| 4 | B `message_and_await_agent(msg2)` — wakes A (payload: `[msg2]`); B blocks | `[B:msg]` | A |
| 5 | C `enter_conversation(sender="Claude-C")` — C migrates from their home Z to X; C added; queued; Z ends | `[B:msg, C:enter]` | A |
| 6 | A `message_and_await_agent(msg3)` — wakes B (payload: `[msg3]`); A blocks | `[C:enter, A:msg]` | B |
| 7 | B `message_and_await_agent(msg4)` — wakes C (payload: full history `[msg1..msg4]`, since C is newly added); B blocks | `[A:msg, B:msg]` | C |
| 8 | C `message_and_await_agent(msg5)` — wakes A (payload: `[msg4, msg5]` — delta since A's `last_seen_seq=3`, no own-emissions to filter); C blocks | `[B:msg, C:msg]` | A |
<!-- markdownlint-enable MD060 -->

#### Edge cases

- **Last-alive-member calls `message_and_await_agent`**: if no alive peer remains (only dormant members or solo), the handler returns `__CONVERSATION_EMPTY__\n<parting messages from members who left since caller last spoke, chronological>` immediately. Caller is removed per the session-fallback rule. If no dormant members remain either, conversation transitions to Ended.
- **Force-end** (John triggers via phone): all `QueueEntry.future`s resolve with `__CONVERSATION_ENDED__\n<final notice>`; `members_active` clears (every member, alive or dormant, falls back per the session-fallback rule — alive sessions re-bind to home/unbound; dormant sessions are released from the conversation but their CLI sessions, where they exist, can still be re-spawned via the normal flow). Conversation transitions to Ended. The dispatch loop handling `force_end_conversation_commands/` is idempotent — a command targeting an already-Ended conversation is a no-op.
- **SessionEnd (orderly process exit)**: cli-session-end-hook fires; server marks the member dormant (`alive = False`, `session_ended_at = now()`, `session_end_reason` set). Conversation stays Active (members aren't removed for dormancy). If the SessionEnd reason was `clear` or `compact`, the member is additionally marked `session_lost_permanently = True` and the underlying CLI session can never be revived.
- **Member crash / process death (SIGKILL, BSOD, network loss)**: no SessionEnd hook fires. The member's `alive` flag stays True from the server's view (stale-alive). Their tool call times out at the MCP 24h timeout; the conversation stays "stuck" with a stale-alive member until either (a) the surviving members force-end, or (b) the server restarts. T-003 (collab session GC) addresses this with a stale-alive sweeper.
- **`enter_conversation()` with no openConversation AND caller unbound**: returns `"ERROR: no open conversation. Ask John to open one on the phone, or have an agent already in a conversation call open_conversation."`. Caller's session remains unbound.

### Away mode (simplified, global only)

- Single Firebase node: `global_settings/away_mode: bool`. Server caches in-memory.
- `set_away_mode(bool)` is the mutation path from agents.
- John's phone can also write to `away_mode_commands/` with `{type: "set", value: bool, issued_at}`; existing dispatch loop applies the change.
- **Spawn from the phone auto-enables away mode** if currently False (per T-027). The phone surfaces a confirmation toast on the auto-enable.
- **Session-fallback rule references away mode**: when a session is removed from a conversation (leave / force-end / etc.), if away mode is on, the session re-binds to its home conversation; if off, the session becomes unbound and at-desk-redirected. See Conversation model section.
- **Implications of global-only**:
  - `Registry._cwd_overrides` and all related plumbing removed.
  - Per-cwd pill chip on Page B removed.
  - Per-cwd swipe-to-flip gesture on Page A removed.
  - The bulk-respond modal on a per-cwd exit (existed to handle pending questions when flipping off a single cwd) is removed; only the global-exit bulk-respond stays.

### Firebase schema

Hard cutover: `channels/<cwd_key>/...` deleted; `conversations/<id>/...` replaces it. Field-by-field mapping below preserves every existing `ChannelMessage` field; nothing visible on the phone is dropped.

<!-- Firebase RTDB schema tree is illustrative ASCII-art, not a real language. -->
<!-- markdownlint-disable-next-line MD040 -->
```
conversations/<conversation_id>/
  title                       (str)
  state                       "active" | "ended"
  continued_from              (conversation_id | null — set on resume-spawn)
  created_at                  (iso-8601 str)
  last_activity_at            (iso-8601 str)
  ended_at                    (iso-8601 str | null)
  hidden                      (bool)
  preview                     (str — latest message snippet)
  unread_count                (int)
  pending_responses           (int — badge count)

  members_active/<sender>/
    cli_session_id            (str — primary routing key)
    sender                    (display name; agent-supplied)
    cwd                       (canonical cwd, informational)
    surface                   "windows" | "wsl"
    alive                     (bool — False when session is dormant)
    session_lost_permanently  (bool — True on clear/compact SessionEnd)
    session_ended_at          (iso-8601 | null)
    session_end_reason        (str | null)
    joined_at
    last_seen_seq             (int)

  members_history/<push_id>/  # explicit-leave + force-end departures only (NOT dormancy)
    cli_session_id
    sender
    cwd
    surface
    joined_at
    left_at                   (iso-8601 once left)
    parting_msg_id            (str | null — link into messages/)

  messages/<msg_id>/          # every existing ChannelMessage field, verbatim
    sender                    (str)
    type                      "agent_msg" | "question" | "response" | "notify" | "document" | "parting" | "system"
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

cli_sessions/<session_id>/    # NEW — per-session metadata for fallback / resume
  home_conversation_id        (conversation_id — set on first switchboard contact)

global_settings/
  away_mode                   (bool — single source of truth)
  open_conversation_id        (conversation_id | null — singleton agent-joinable pointer, mirror of in-memory _open_conversation_id)

away_mode_commands/<push_id>/
  type                        "set"
  value                       (bool)
  issued_at

force_end_conversation_commands/<push_id>/
  conversation_id
  issued_at

combine_commands/<push_id>/   # NEW — phone-side trigger for the combine flow
  source_conversation_id
  target_conversation_id
  issued_at

spawn_commands/<push_id>/     # see T-027 for full schema
  type                        "fresh" | "resume"
  surface                     "windows" | "wsl"
  project                     (str)
  prompt                      (str | null)
  target_conversation_id      (str | null)
  source_conversation_id      (str — resume only)
  issued_at
```

(The earlier draft of this schema included an `inject_queue/<conversation_id>/...` node; the inject-queue mechanism was retired during implementation. See the **Implementation deltas** section.)

**Message type values** (`messages/<msg_id>/type`):

- `agent_msg` — `message_and_await_agent` speak event
- `question` — `ask_human` question. The at-desk-redirect path writes the same content with `type="notify"` instead (no `request_id`, no `suggestions`) so the phone surfaces it as a passive notification.
- `response` — John's reply to a `question`
- `notify` — `notify_human` emission, or an at-desk-redirected `ask_human`
- `document` — `send_document_human` delivery
- `parting` — `leave_conversation` message
- `system` — server-written message (combine "Merged with…", "Merged into…", spawn ack, resume opening, dormancy notification, etc.)

(Earlier drafts of this design used `ask` / `doc` for the question / document types; the implemented names are `question` / `document`. See the **Implementation deltas** section.)

**Removed Firebase nodes**:

- `channels/<cwd_key>/...` (entire subtree)
- `away_mode_commands` of types `enter_cwd` / `exit_cwd` (only `set` remains)
- Any per-cwd away-mode override storage
- `conversations/<id>/collab` field (no longer a creation-time attribute)

### Android UI

- **Page A (conversations list)** — replaces channel list. One row per conversation (Active + recently Ended). Row content: title, preview, last-activity, unread/pending badge, optional "open" indicator when `conversations/<id>/id == global_settings/open_conversation_id`. "Show hidden" toggle in the overflow menu retained.
- **Page A long-press menu** — Resume, Combine into…, Hide / Unhide, End conversation. Most items are defined by T-027 (Resume, Combine into…, surface-aware behavior). End conversation force-ends the Active conversation; per the session-fallback rule, members are not orphaned by force-end — they fall back to their home (away on) or terminal (away off). Confirmation dialog reflects this.
- **Page A FAB** — Spawn dialog (defined by T-027). Restores the spawn entrypoint that this design's prior version removed.
- **Page B (conversation view)** — replaces channel view. Title bar shows the conversation title plus a sub-line listing alive + dormant members (e.g. `Claude-Win (C:\Work\Switchboard), Claude-WSL (/home/john/work/switchboard, dormant)`). Bubble feed renders messages in chronological order with per-sender attribution. Reply input visible when a pending `ask_human` exists in the conversation, attributed to the asking agent. For continuations (`continued_from` set), a header chip surfaces the link to the predecessor.
- **Page A row swipe gestures** — both gestures use `SwipeToDismissBox`, both snap back, both raise a confirmation dialog before mutating:
  - **Swipe right** → end conversation. Enabled when `conversation.state == "active"`. Confirmation dialog: `"End conversation '<title>'? Members will fall back to their home conversation (if away mode on) or to terminal output (if off)."`. On confirm → phone writes a `force_end_conversation_commands/<push_id>/{conversation_id, issued_at}` record; server-side dispatch loop ends the conversation with session-fallback semantics.
  - **Swipe left** → hide. Unchanged from prior version of this design.
- **No per-conversation away pill anywhere.** Only the global pill chip on Page A app-bar remains, and it writes a `set` command to `away_mode_commands/`.
- **Agent status**: one row per conversation; binds to `conversations/<id>/agent_status/`. Renders the current stick-holder's state.
- **Spawn / Resume / Combine UI** — see [T-027](2026-05-20-spawn-conversation-aware-redesign-design.md) for the spawn FAB, spawn dialog, resume dialog, and combine target-picker.
- **Bulk-respond modal**: only the global-exit variant remains. Per-channel exit variant retired.

### Hooks

Four hooks bundled in the switchboard plugin (`hooks/hooks.json`):

- **PreToolUse: `cli-session-injector-hook.py`** (NEW, T-027) — fires on every tool call, no matcher. Self-filters on `tool_name.startswith("mcp__switchboard__")`. For matching tools, reads `session_id` and `cwd` from hook-event input JSON and emits `hookSpecificOutput.updatedInput` merging `cli_session_id` and `cwd` into the tool's input. The agent never knows or passes session_id; the hook does it transparently. (Empirically verified: `updatedInput` REPLACES the original input, so the hook explicitly copies every original `tool_input` field plus the injected ones. See T-027's verification scripts.)
- **PreToolUse: `agent-status-hook.py`** (existing) — writes to `conversations/<id>/agent_status/` after server-side `_session_to_conversation_id[cli_session_id]` lookup. Server gates the write: only the stick holder's status update lands; non-stick-holder writes are no-ops. Quiet-when-at-desk gate continues to apply.
- **SessionEnd: `cli-session-end-hook.py`** (NEW, T-027) — fires on orderly Claude exit (`/exit`, Ctrl+D, terminal closed gracefully). POSTs to a new server endpoint `POST /cli-session/end` with `{session_id, reason}`. Server marks the corresponding member dormant (NOT auto-leave): `alive = False`, `session_ended_at = now()`, `session_end_reason` set. For `clear`/`compact` reasons, additionally sets `session_lost_permanently = True`. Conversation stays Active.
- **Stop / UserPromptSubmit / PostToolUse: `turn-end-hook-away-mode.py` + `agent-status-hook.py`** (existing) — turn-end gating and agent-status updates. Turn-end blocks if `global_away_mode == True` OR if the agent's `cli_session_id` is currently in an Active conversation. Block message: `"you're in conversation <id>; call leave_conversation first (or message_and_await_agent if there's still active dialog)."`. Server endpoint returns `(global_away_mode, current_conversation_id)` from the hook's POST.

### SKILL.md

Major rewrite. Sections that change:

- **CRITICAL: Away Mode Protocol**: rewrites around single global flag + `set_away_mode(bool)`. "User-managed flag" rule preserved — agents only flip on explicit signal in the most recent prompt. Note that spawn from the phone auto-enables away mode, so agents launched from the phone start in away mode without needing to call `set_away_mode` themselves.
- **Switchboard MCP Tools** tool list:
  - Agents no longer pass `channel`. The plugin's `cli-session-injector-hook.py` PreToolUse hook injects `cli_session_id` and `cwd` automatically.
  - Agents DO pass `sender` (their display name — agent-supplied, no uniqueness enforced; pick a unique short name with optional guidance from John in the spawn prompt).
  - Add `open_conversation(sender, title?)`, `enter_conversation(sender)`, `combine_conversations(source_id, target_id)`, `lookup_conversation_ids(cwd?, sender_contains?, title_contains?)`.
  - Add `leave_conversation(sender, parting_message)` — note: no "cannot leave while in away mode" gate. The session-fallback rule routes the leaving session back to its home conversation (away on) or to unbound terminal output (away off).
  - Add `set_away_mode(value)`.
  - Remove `end_collab`, `enter_away_mode(cwd)`, `exit_away_mode(cwd)`.
  - Update `message_and_await_agent` description (errors if not in conversation; message required and non-empty; payload semantics; the "listen without speaking" use case is served by `enter_conversation()` instead).
- **Conversations section (new, sizeable)**: the conversation model (Active / Ended + openConversationId pointer); member states (alive / dormant / permanently lost); the session-fallback rule (leave / force-end paths); talking-stick FIFO; payload semantics; sentinel returns (`__CONVERSATION_EMPTY__`, `__CONVERSATION_ENDED__`).
- **Collab composition patterns (new section)**: three patterns documented:
  1. **Invite-then-join** — agent A calls `open_conversation(sender, title)` to make their conversation the open one; agent B calls `enter_conversation(sender)` to migrate in.
  2. **Combine** — agent (or John from phone) calls `combine_conversations(source_id, target_id)` to merge two existing conversations.
  3. **Spawn-into-existing** — John spawns a new agent from the phone directly into an existing conversation via the spawn dialog's "Add to existing" option.
- **Sender naming guidance**: "Pick a unique short name. If John named you in your prompt, use that. Otherwise pick something distinct from the existing roster (visible in your prompt for join-existing spawns; otherwise ask via `lookup_conversation_ids` if useful)."
- **Spawn flow description**: defer to T-027.

### Spawn (paired design)

The spawn redesign is in [T-027 paired design](2026-05-20-spawn-conversation-aware-redesign-design.md), shipping together with this conversations redesign. Covers:

- The spawn FAB and dialog (Windows / WSL surface picker, project picker, optional prompt, optional add-to-existing-conversation).
- Single-agent spawns; multi-agent collabs composed via the `open_conversation()` + `enter_conversation()` prompt mechanic, or via spawn-into-existing, or via combine.
- Resume mechanic (long-press a row whose members are dormant → resume via `claude --resume <session_id>` per member, into a new continuation conversation with `continued_from = source.id`).
- Combine mechanic (long-press → "Combine into…" → target picker; or `combine_conversations` MCP tool from terminal).
- Auto-enable of `global_away_mode` on spawn dispatch.
- Session-file aging warning indicator on Page A rows whose youngest member's `session_ended_at` crosses 25 days (Claude Code's default `cleanupPeriodDays = 30` window).

The earlier "spawn UI removed; server-side spawn kept as no-op" stance in prior drafts of this design retires — T-027 brings spawn back as the primary entry point for conversation composition.

### Migration

Hard cutover. On first deploy:

1. Server startup wipes the `channels/` subtree from Firebase (one-time idempotent delete).
2. New `conversations/` subtree begins empty.
3. All Android clients require a fresh install / app-data-clear to drop stale local cache from the old schema.

No legacy data preservation. Existing logs in `logs/switchboard.jsonl` remain intact as historical reference.

## Testing strategy

In-process integration tests (matching the existing `tests/` style) cover:

- **Lifecycle**: Active conversation creation via first `xxx_human` (auto-create), Active creation via `open_conversation` (set openConversationId), Ended via last-alive-member leave with no dormant remaining, Ended via force-end (with session-fallback for each member), Ended via combine source-side. Confirm dormant members keep conversation Active.
- **Talking-stick FIFO**: 2-agent ping-pong, 3-agent rotation with mid-stream joiner via `enter_conversation`, last-alive-member sentinel, cross-member ask_human visibility.
- **Routing**: session_id → conversation lookup via `_session_to_conversation_id`, cross-cwd conversations (Windows + WSL members in one conversation), session-fallback rule on leave (re-bind to home when away on; unbind when away off).
- **`enter_conversation` branches**: caller already in conversation X → queue in X without speak; caller unbound + openConversation set → join open + queue; caller in X ≠ openConversation + open set → migrate X→open + queue; caller in X + no open → just queue (no migration); caller unbound + no open → error.
- **`open_conversation` semantics**: caller not in any conversation → error; caller in X → openConversationId set to X; pre-existing open Y → openConversationId replaced (Y no longer agent-joinable, but stays Active).
- **Combine flow**: source's alive members rewire; dormant members revived via `claude --resume`; permanently-lost members stay in source; source ends; target gets system marker; intro inject lands in `inject_queue/<target>/`; concurrent combine on disjoint pairs serializes correctly via lock ordering.
- **SessionEnd handling**: orderly `/exit` marks member dormant; conversation stays Active; `clear`/`compact` reasons additionally mark `session_lost_permanently`.
- **Away mode**: global flag transitions; at-desk-redirect on `ask_human`/`notify_human` while creating + logging the conversation entry; spawn dispatch auto-enables away mode (verified in T-027 tests).
- **Tool errors**: empty `message` rejection on `message_and_await_agent`; `enter_conversation` with no open + caller unbound; `open_conversation` from a session not in any conversation; `combine_conversations` with same source and target.
- **Hook contract**: `cli-session-injector-hook.py` merges `cli_session_id` and `cwd` into `updatedInput` correctly; self-filters non-switchboard tools; `cli-session-end-hook.py` POSTs the correct payload shape.

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
- **Partner-title-change relay** (`title_tracker.maybe_prepend`) — **NOT preserved.** The `TitleTracker` was deleted during implementation; its `maybe_prepend` mechanism turned out to have been dead code (instantiated but never invoked) even before this redesign. Title shifts are now conveyed through `Conversation.title` (Page A header) and the per-message `title` field, plus the agent's own message text. See the **Implementation deltas** section.
- **`ask_human` supersede semantics** — if a new `ask_human` arrives for `(conversation_id, sender)` while one is pending, the prior future is cancelled and the prior question's Firebase entry is marked `cancelled=true`. Today this is keyed by `(cwd, sender)` — same logic, new key.
- **MCP stateful HTTP transport** + **cancellation propagation** — `stateless_http=False` preserved; `notifications/cancelled` from Claude Code continues to mark in-flight `ask_human` and `message_and_await_agent` questions `cancelled=true`. The Gemini CLI's lack of cancel propagation is a known limitation that doesn't worsen here.
- **24-hour MCP timeout** — unchanged.
- **Spawn flow** (server side only) — `server/spawn.py`, `server/gateway/handlers.py` spawn closures, `SwitchboardSpawn` scheduled task, `scripts/spawn-launcher.ps1`, `quser` no-login gate (post-2026-05-02) all stay intact. The phone simply cannot trigger them while the client UI is gone (T-027 redesigns).
- **At-desk message-still-creates-channel-and-logs** behavior for `ask_human` redirect (T-023) — extends to `notify_human` in this design (both gated), `send_document_human` continues to deliver regardless of away-mode state.
- **Rate limiter** for `notify_human` + `send_document_human` — `RateLimiter`'s shape is unchanged; the key changes from `canonical_cwd` to `conversation_id` so the bucket is scoped to "this conversation" rather than "this channel." If we want per-sender granularity later, the key generalizes naturally (`f"{conversation_id}:{sender}"`); not done in v1 since the channel-level bucket suffices.
- **Bulk-respond modal** — global-scope variant survives (the dialog the phone shows when flipping global away → at-desk while pending questions exist). Logic in `_apply_bulk_respond_decision` keeps its `send_default` / `skip` / `cancel` decision shape; `scope_cwd` parameter retires (always None / global).
- **Inject queue** — **NOT preserved.** `dispatch_inject_queue`, the `InjectPort` protocol, `start_inject_listener`, `poll_inject_messages`, and the `channels/{session_id}/inject_queue` Firebase node were all retired during implementation. The legacy CollabSession BYO flow that consumed these no longer exists, and no phone-side producer was emitting to the queue. Human-injected messages from the phone compose box now travel via the standard `/responses/...` and `/conversations/<id>/answers/...` paths (the latter for the new conversation-id-keyed answer route). See the **Implementation deltas** section.
- **Agent-status hook + pulsing channel-list dot + inline status row** (T-139) — preserved at the conversation level. Hook writes filter to the stick-holder only; phone shows one status indicator per conversation.
- **`logs/sessions/<channel_key>.log`** per-channel session log — repurposed to `logs/sessions/<conversation_id>.log`; `_append_session_log` helper retargets accordingly.
- **`canonicalize_cwd`** logic (post-b889472) — unchanged. Two-cwd collab works because cwd is now just member identity; the conversation ID is the routing key.
- **`PendingRequest.msg_id` linkage** — links a question's Firebase msg_id to the pending entry so replies can set `attached_to_msg_id` for in-line reply rendering. Preserved verbatim, now scoped under the conversation.

### Deliberately retired (with replacement)

- **`CollabSession` class** (`server/collab.py`) — replaced by `Conversation`.
- **Open / Closed conversation states** — retired. Replaced by `Active | Ended` plus the `openConversationId` global pointer. The "Open as a creation-time decision" model retires; agents promote any Active conversation to be open via `open_conversation()`.
- **`collab: bool` attribute** on `Conversation` — retired. Multi-member is a runtime fact, not a creation-time decision.
- **Channel-as-cwd routing** — retired. `_channel_to_conversation_id: dict[canonical_cwd, conversation_id]` becomes `_session_to_conversation_id: dict[cli_session_id, conversation_id]`. Canonicalization is display-only.
- **Seed-blocks-until-second mechanic** — retired. Agents promoting their conversation via `open_conversation()` don't block; joiners migrate in via `enter_conversation()` without a seed timeout.
- **Seed timeout** — retired (no more seed concept).
- **"Non-collab + global away-flip-off ending all Closed conversations"** trigger — retired (no Closed state).
- **"Cannot leave conversation while in away mode" guard** — retired. Session-fallback rule routes leaving sessions to their home conversation (away on) or to unbound terminal output (away off); members are never orphaned.
- **Registry surfaces tied to old model** — retired: `_sessions`, `_cwd_overrides`, `_recently_ended`, `_last_messaging_sender`, `is_away_mode_active(cwd)`, `set_cwd_override`, `remove_cwd_override`, `update_cwd_override_cache`, `cwd_overrides()`, `record_messaging_sender`, `last_messaging_sender_for`, `get_collab_baton_holder`.
- **`dispatch_away_mode_commands` command types** — `enter_global` / `exit_global` / `enter_cwd` / `exit_cwd` retire; replaced by a single `set` type. `_clear_all_cwd_overrides` retires entirely.
- **`bulk_respond.py` `scope_cwd` parameter** — retired; only global scope remains.
- **`end_collab` tool + reporter handoff logic** — retired; `leave_conversation` covers single-agent exit, last-one-left handling, and the empty-conversation cleanup.
- **BYO implicit enrollment** (auto-pairing of two same-cwd agents via parallel `message_and_await_agent` calls) — retired; collab composition is via the explicit `open_conversation` / `enter_conversation` / `combine_conversations` / spawn-into-existing flows.
- **Per-cwd away-mode UI** — pill chip on Page B (long-press toggle), the swipe-to-flip-at-desk gesture, the per-channel bulk-respond dialog — all retired.
- **`enter_away_mode(cwd)` / `exit_away_mode(cwd)`** tools — retired; collapsed into `set_away_mode(bool)`.
- **Agent-supplied `channel` parameter** on every switchboard MCP tool — retired. Hook-injected `cli_session_id` and `cwd` replace it.
- **The "spawn UI removed pending T-027" stance** from this design's earlier draft — retired now that T-027 ships paired with this design. Spawn UI returns.

### Newly added (no prior equivalent)

- **`force_end_conversation_commands` Firebase node** + dispatch loop. Phone-side trigger for force-ending an Active conversation. Per the session-fallback rule, force-end no longer orphans members.
- **`combine_commands` Firebase node** + dispatch loop. Phone-side trigger for the combine mechanic (defined in T-027).
- **`global_settings/open_conversation_id`** Firebase node (mirror of in-memory `_open_conversation_id`).
- **`cli_sessions/<session_id>/home_conversation_id`** Firebase node (per-session home pointer used by session-fallback).
- **PreToolUse hook `cli-session-injector-hook.py`** (T-027). Injects `cli_session_id` and `cwd` into every switchboard MCP call.
- **SessionEnd hook `cli-session-end-hook.py`** + server endpoint `POST /cli-session/end` (T-027). Marks the member dormant (not auto-leave).
- **MCP tools:** `open_conversation`, `combine_conversations`, `lookup_conversation_ids`. `enter_conversation` significantly modified (unified "join + listen for intro").
- **Confirmation dialog on swipe-to-hide** — today the gesture mutates immediately; the spec adds the confirmation step.
- **Session-fallback rule** governs leave / force-end behavior — members re-bound to their home conversation (away on) or unbound (away off).

### Known regressions, accepted

- **Existing conversation history is wiped** on cutover. No migration of `channels/<cwd_key>/messages/...` into `conversations/<id>/messages/...`. Stored history pre-cutover lives only in `logs/switchboard.jsonl` and per-channel session logs.
- **Server crash during an Active multi-member conversation loses queue state** (matches today's constraint). Blocked agents time out at the 24h MCP timeout; the Conversation is left "stuck" in Firebase as Active with phantom members until force-ended or persistence (T-001) ships.
- **Gemini agents temporarily lose Switchboard access** until Gemini's hook system gains an equivalent PreToolUse-injection capability. They can resume access once the hook plumbing matures on the Gemini side.
- **Older Claude installs without the plugin's hook can't call Switchboard** once this design ships — the MCP boundary rejects calls missing `cli_session_id`. Plugin install is the documented path.
- **SessionEnd `clear`/`compact` reasons mark CLI sessions permanently lost** — affected members are non-revivable via resume or combine.
- **30-day session-file aging** (Claude Code's `cleanupPeriodDays` default; see T-027). Conversations with members dormant 31+ days become non-resumable when their CLI session files get pruned.

## Open questions / future work

- **Cross-host A2A** (T-025) — remains low priority. The session_id-based routing key is naturally cross-host compatible (UUIDs are globally unique); Firebase transport is the remaining work.
- **Persistence layer** (T-001) — would convert the "never restart during multi-member conversation" operational rule into a soft-loss. Out of scope here but enabled by this design.
- **Garbage collection** (T-003) — sweeper for stale-alive members (no SessionEnd hook fired due to crash/SIGKILL/BSOD/network loss). Worth picking up after this lands.
- **Per-sender rate limiting within a conversation** — v1 scopes the rate-limit bucket to `conversation_id`; if abuse patterns surface, regrade to `(conversation_id, sender)` keys.
- **Visual indicator for `openConversationId` on Page A** — which row is currently the open one? Some visual treatment (badge, accent border) is probably needed for the prompt mechanic to be discoverable. v1 sketches the data model; UX treatment is left to plan-stage refinement.
- **`close_conversation` tool** — explicit "this conversation no longer wants new joiners" tool that clears `openConversationId`. v1 relies on the implicit-clear behaviors. Add if friction surfaces.
- **Agent-driven cross-conversation move to non-open target** — v1 has `enter_conversation()` (move to the open one) but no general "move to arbitrary conversation" affordance for agents. Server can do this via spawn-into-existing for a new agent, but agent-driven self-migration to a non-open target isn't a tool. Defer.

## Implementation deltas

**Recorded 2026-05-26** after the branch-review audit that followed commit `c44b632`. The design above is the authoritative target; this section captures where reality landed differently. Inline forward-pointers above link back here.

### Schema and tool surface

- **Message `type` values** — implemented as `question` / `notify` / `document` / `agent_msg` / `system` / `parting` / `response` rather than the spec's earlier `ask` / `notify` / `doc` / `agent_msg` / `system` / `parting` / `response`. Renames were considered and rejected during implementation: the cost of churning Firebase listeners, Android consumers, and FCM payload code wasn't justified by the naming cleanup. The spec above has been updated to match.
- **Error string wording** — `enter_conversation`'s "no open conversation" error and `message_and_await_agent`'s "message is required" error are slightly longer in the implementation than the spec quoted. The spec above has been updated; agents pattern-match on the `"ERROR:"` prefix in practice, so historical literal matches still work.
- **`bulk_respond.py` `scope_cwd` parameter** — spec says retired; reality is the parameter is **dead code** (caller at `dispatch.py` hardcodes `scope_cwd=None`; the non-None branch in `_apply_bulk_respond_decision` is unreachable). A trivial follow-up can delete the parameter and the unreachable branch.
- **`pending_questions/<request_id>/` and `answered_question_msg_ids/<msg_id>/`** — these subtrees were specced (lines 349-356 of this design) but initially never written. Added under Fix Pack 2 on 2026-05-26: `MessageWriter.add_pending_question_record` / `remove_pending_question_record` / `mark_question_answered` are now wired into `ask_human`'s lifecycle. Not rehydrated on restart by design — stale entries naturally clear when the listeners reattach and replay the answers.
- **`members_history` Firebase persistence** — `ConversationMember` is appended in-memory at `leave_conversation` and `open_conversation` rename branches; the matching Firebase write at `/conversations/<id>/members_history/<sender>` was added under Fix Pack 2 (new abstract `MessageWriter.write_conversation_member_history` method + `FirebaseBackend` implementation + hydration restore). Before that fix, the in-memory append was lost across restart.

### Retired features that didn't make it back

- **`title_tracker.maybe_prepend`** — retired. The TitleTracker class was deleted as dead code (instantiated, never invoked) during implementation. Title shifts now ride on `Conversation.title` (Page A header), the per-message `title` field, and the agent's own message body.
- **`inject_queue`** — retired in full. The `InjectPort` protocol, `start_inject_listener`, `poll_inject_messages`, `dispatch_inject_queue`, and the `channels/{session_id}/inject_queue` Firebase node were all removed in Fix Pack 3. The legacy CollabSession BYO consumer no longer exists, and no Android producer was ever wired to write there in the conversation-keyed model.
- **Spec line 31 caveat** — the "spawn is removed from the Android client in this work; server-side spawn code preserved minimally" stance from this design's earlier draft is **fully retired**: T-027 shipped paired with this design (commit `c44b632`) and brings the spawn UI back as the primary entry point for conversation composition.

### Agent-status hook

- **Hook script still keys on `cwd`** ([`scripts/agent-status-hook.py:84-86`](../../../scripts/agent-status-hook.py)) — the hook reads the agent's working directory from its hook-event input and includes it in the POST body. The server-side handler resolves the `(cli_session_id, cwd)` pair to a conversation_id before writing. Spec text (line 526 above, "preserved at the conversation level") is accurate at the end-state level — one indicator per conversation — but the hook's transport key is still cwd; a follow-up could simplify to send `cli_session_id` only.

### Known unresolved bugs in the implemented mechanism

- **Combine wait-queue leak.** When an alive member is blocked in `message_and_await_agent` in source and `combine_conversations` migrates them to target, their `wait_entry` is left in `source.wait_queue` while the member object moves to `target.members_active`. Source then ends; nothing resolves the future. The blocked agent times out at the 24h MCP timeout. Discovered in the branch review; fix is to drain or relocate the moved member's wait_entries during the combine pass.
- **Stale-alive members** — per spec line 279 (process death without SessionEnd). Still unresolved; tracked under T-003 (collab session GC).

### What the spec got right and shipped cleanly

To balance the deltas: routing-by-`cli_session_id`, the `Conversation` model, lifecycle states, member-state transitions, `apply_fallback` session-fallback rule (now correctly applied on `__CONVERSATION_EMPTY__` AND on force-end of dormant members AND on `cli_session_end` of blocked peers), the `open_conversation` → `enter_conversation` handshake, the 5-branch `enter_conversation` table, combine with auto-resume of dormant members via spawn-pending files, the talking-stick FIFO via `wait_queue` + `_wake_one_from`, the wake-payload semantics, the hook injector contract, `cli-session-end-hook.py` → dormant marker pipeline, hydration of conversations / members / routing maps / `cli_sessions/<id>/home_conversation_id` / `global_settings/open_conversation_id` — all shipped per the design.

## Supersedes / relates to

- **Pairs with**: [`2026-05-20-spawn-conversation-aware-redesign-design.md`](2026-05-20-spawn-conversation-aware-redesign-design.md) (T-027). The two designs ship together; T-027 brings the spawn UI back, defines the combine and resume mechanics, and drove the state-model collapse + session_id-as-channel amendments folded into this design.
- **Supersedes**: the implicit BYO-collab pattern in [`2026-04-23-bring-your-own-session-design.md`](2026-04-23-bring-your-own-session-design.md); the per-cwd away-mode override path in [`2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md`](2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md); the Firebase schema in [`2026-04-28-away-mode-firebase-schema-reorg-design.md`](2026-04-28-away-mode-firebase-schema-reorg-design.md) (replaced by the conversations subtree).
- **Relates to**: T-025 (cross-host A2A; same-host narrow case is in scope here; routing-key problem resolved by session_id), T-026 (cwd canonicalization gap; routing-layer aspects resolved here, only display-layer remains), T-001 (persistence; future fix), T-003 (stale-alive member GC; addresses the residual crash failure mode), T-028 (narrower alternative — surgical FIFO refactor inside `CollabSession`; subsumed by this design's wholesale collapse and no longer relevant as an alternative).
