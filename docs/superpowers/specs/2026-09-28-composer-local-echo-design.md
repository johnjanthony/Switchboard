# T-267: Composer local echo and delivery states - Design

**Date:** 2026-09-28
**Status:** Approved (design sections 1-4 approved by John at the terminal, 2026-09-28)
**Backlog:** T-267. Builds on T-164 (free-form message injection, `69b89cd`). Related: T-265 (Operator has no component-test harness, so render wiring stays manually verified).

## Problem

Both composers (Android `sendMessageToConversation`, Operator `sendMessage`) push a command to `/message_commands` and render nothing. The message appears on either surface only when the server consumes the command and writes the `type: "human"` row to `/messages/<conv_id>`. Between send and write-back there is no echo, no pending state, no error and no timeout, so a dead delivery path (service down, dispatch loop stalled, listener dead) looks exactly like a slow one. During T-164's live checks a message took about a minute to appear because the deployed service had no `message_commands` listener at all; diagnosing it needed `/healthz` and an RTDB read.

## Decisions (John, 2026-09-28)

1. **Correlation:** the server copies the command's RTDB push key into the message row it writes, as `command_id`. The client matches its pending row to the delivered row exactly. (Rejected: client-only text matching, which is ambiguous for identical sends and flickers.)
2. **Stuck send:** after about 20s unconsumed, the row turns amber, explains that the message delivers if the server returns within 10 minutes, and offers Cancel. (Rejected: warn with no action; failing the send client-side after the timeout.)
3. **Source of pending rows:** the command queue itself (`/message_commands`), not per-surface local state, so pending rows survive restart/reload and show on both surfaces.

## Section 1 - Server and data contract

### The listener passes the key (opt-in)

`FirebaseBackend._start_command_listener` gains `pass_command_id: bool = False`. When set, the handler is called as `handler(cmd, command_id=cmd_id)`; otherwise the call is unchanged. Only `start_message_command_listener` sets it, so the combine, force-end, spawn, convene and away-mode handlers are untouched. The key comes from RTDB rather than a client-written field, so it is authoritative and works for client builds that predate this change.

### `command_id` through the delivery ladder

`dispatch_message_commands._handle(cmd, command_id=None, ack=None)` passes it to `deliver_human_message(..., command_id=command_id)`. When `command_id` is not None, `deliver_human_message` adds it to the in-memory message dict appended to `conv.messages` and passes it to `write_conversation_message(..., command_id=...)`, whose expanded form writes the field on the `/messages/<conv_id>/<push>` row. The field is additive: Android's `ChannelMessage` is `@IgnoreExtraProperties` and Operator reads rows loosely, so older clients ignore it.

### Write before delete

Today `deliver_human_message` schedules the human-message write with `_spawn_bg` and returns, and the listener deletes the command once the handler returns. The delete can therefore land before the row, and a failed background write loses the message from RTDB while the command is deleted anyway. Change: keep the `_spawn_bg` call where it is, keep the returned task, and `await` it after the conversation lock is released, before `deliver_human_message` returns. A failed write then raises out of the handler, `_report_command_failure` runs, and the command stays queued to replay on restart: the same at-least-once contract the other idempotent handlers have. Accepted consequence: agents already woken by the first attempt receive the message again on replay.

### What a client can conclude

| Command entry | Row with `command_id == K` | Meaning |
|:--|:--|:--|
| present | - | not yet consumed |
| gone | present | delivered |
| gone | absent after a 5s grace | not delivered: conversation not active, dropped as stale on restart, or invalid (the server already sends an admin notice for each), or cancelled from another surface |

### Out of scope

De-duplicating a replayed command after a crash. `command_id` makes it possible later; nothing here depends on it.

## Section 2 - Client state model (identical on both surfaces)

### Inputs

Each client keeps a listener on the whole `/message_commands` node (small: entries are deleted once consumed) and on `.info/connected`. One pure function per surface derives the pending rows for the open conversation:

`derivePendingSends(queued, messages, seenQueued, now, connected)`

- `queued`: the node's entries, filtered to this conversation's `conversation_id`; entries missing `conversation_id` or `text` are ignored.
- `messages`: the conversation's rows; the set of their `command_id` values decides delivery.
- `seenQueued`: in-memory map of keys this surface has seen queued, each with `{conversationId, text, issuedAt, goneAt}`. `goneAt` is stamped when the key leaves the queue.
- `now`, `connected`.

Android implements it in `android/shared/.../PendingSendPolicy.kt`, Operator in `dashboard/derive.js`.

### States

Evaluated in this precedence order; the first match wins.

| State | Condition | Row shows | Actions |
|:--|:--|:--|:--|
| expired | entry present, age >= 600s | "Expired: the server will drop this, not deliver it." | Retry, Discard |
| offline | entry present, `connected == false` | "Offline: sends when you reconnect." | Cancel |
| not picked up | entry present, age >= 20s | amber: "Server hasn't taken this. It delivers if the server returns within 10 min." | Cancel |
| sending | entry present, age < 20s | muted "sending..." | - |
| delivered | entry gone, row with `command_id == K` exists | nothing; the real row takes over and the key leaves `seenQueued` | - |
| not delivered | key in `seenQueued`, entry gone, no matching row, `now - goneAt >= 5s` | "Not delivered", text kept | Retry (only while the conversation is Active), Dismiss |

Age is `now - issued_at`. Expired outranks offline because a write queued offline for more than 10 minutes carries its original `issued_at` and the server will drop it as stale on arrival.

### Actions

- **Cancel** deletes `message_commands/K`, removes K from `seenQueued` (so this surface does not then show it as not delivered), and puts the text back in this surface's composer: it replaces an empty draft, or is appended on a new line after a non-empty one. Accepted race: if the server takes the command in the instant before the delete, the message still delivers and the restored draft is John's to clear.
- **Retry** pushes a fresh command (new key, new `issued_at`) with the same text and removes the old key from `seenQueued`. From the expired state it also deletes the old entry first, so a later restart does not produce a stale-drop notice for it.
- **Discard** (expired) deletes the entry and removes the key from `seenQueued`.
- **Dismiss** (not delivered) removes the key from `seenQueued`.

### Rules

- **Placement:** pending rows render after the conversation's real messages, oldest `issued_at` first, with sender "John" and `issued_at` as the timestamp.
- **Cross-surface:** a command queued from either surface shows as pending on both, and either can Cancel it. Accepted limitation: a Cancel on one surface shows as "Not delivered" on the other if it saw the entry queued; that is literally true, and Dismiss clears it.
- **Ticker:** a 1s timer runs only while the open conversation has pending rows, so states change on time without new data events.
- **Memory:** `seenQueued` is in-memory only. After an app restart or page reload, entries still queued are rediscovered from the node; a not-delivered row does not survive, and the server's admin notice is the durable record.

### Constants

`SENDING_WINDOW_S = 20`, `NOT_DELIVERED_GRACE_S = 5`, and `COMMAND_TTL_S = 600`. The last mirrors the server's `COMMAND_TTL_SECONDS` in `server/command_freshness.py`, which is not published to clients; each client constant carries a comment naming the server one.

## Section 3 - Surfaces

The pending row looks like John's own message, muted, with a status line and any actions beneath it.

### Operator

- `store.js`: two global listeners beside the widget ones (`message_commands`, `.info/connected`), the `seenQueued` map, and actions `cancelQueuedMessage`, `retryMessage`, `discardQueuedMessage`, `dismissUndelivered`. Writes go through new `commands.js` builders (`{path, value}`, `value: null` for deletes), following the existing pattern and `guardedWrite`.
- `schema.js`: path builders for `message_commands/<key>` and `.info/connected`.
- `ConversationDetail.js`: `Transcript` renders the derived rows after the real messages and above the answer boxes; a conversation whose only content is pending sends shows them instead of the empty-transcript text. The composer draft moves up out of `MessageComposer` into `ConversationDetail` so Cancel can restore text into it.
- A 1s interval runs only while pending rows exist.

### Android (phone)

- `network/Models.kt`: `ChannelMessage` gains `command_id`.
- `MainViewModel`: a `ValueEventListener` on `message_commands` and one on `.info/connected`, exposed as `StateFlow`s, the `seenQueued` map, and the same four actions through `writeReporting`.
- `ConversationViewScreen`: appends the derived rows as extra `LazyColumn` items keyed `pending:<K>`; the scroll-to-bottom effect also fires when a pending row appears; Cancel is handled in the screen, which owns `messageDraft` (T-266); a `LaunchedEffect` ticker runs only while pending rows exist.

### Wear

No UI change. The listener runs in the shared `MainViewModel`, but the watch has no composer and renders nothing from it.

### Excluded

No sending indicator on Page A rows or in the conversation-list preview; pending state lives only inside the open conversation.

## Section 4 - Failure modes, rollout, testing

### Failure modes

- **Queue listener fails** (e.g. permission denied): Operator routes it through the existing global read-error banner; Android logs it and shows no pending rows, which is today's behavior.
- **Cancel / Retry / Discard write fails:** Operator's `guardedWrite` error path; Android's `writeReporting` toast.
- **Malformed queue entries:** ignored by the derivation.
- **Replayed command:** any row carrying the key counts as delivered, so duplicates do not confuse the matching.

### Rollout order

Server first. A new client against an old server would see every command vanish with no `command_id` row and falsely show "Not delivered". Old clients against the new server are unaffected. No text-matching fallback: the order is under our control and the smoke preflight confirms the deployed service before any client is checked live.

### Tests (TDD, one at a time)

- **Server (pytest):** only the message listener receives the key; the key reaches both the in-memory message and the RTDB write; `deliver_human_message` returns only after the write lands; a failing write raises out of the handler, so the command is not deleted.
- **Operator (`node --test`):** `derivePendingSends` for every state and the precedence order, the 5s grace, and "not seen queued, so no not-delivered"; the new `commands.js` builders; the store actions.
- **Android (`:shared:testDebugUnitTest`):** the same state table against `PendingSendPolicy`; `:app:lintDebug`.
- **Smoke harness:** `flow_message_inject` (FLOW 5) also asserts the written human row carries the pushed command's key as `command_id`: the live check of the server contract.

### Live verification

1. Server: restart with `-SkipTests`, `smoke.py --preflight-only`, then `smoke.py --skip-restart`.
2. Operator and the Android emulator: a normal send goes sending -> delivered; emulator airplane mode shows offline, then delivers on reconnect; a command written directly to `/message_commands` for an Ended conversation shows "Not delivered".
3. Not picked up (needs the service stopped, which severs every live MCP session including other agents'): John-gated, asked at the time.
4. Expired (needs the service stopped for over 10 minutes): unit tests only.

The build reaches John's phone only through `scripts/install-client.ps1`, at John's call.

## Out of scope (v1)

- De-duplicating replayed commands (Section 1).
- Publishing `COMMAND_TTL_SECONDS` to clients.
- Pending state outside the open conversation (Page A, previews, Wear).
- An Operator component-test harness (T-265).
