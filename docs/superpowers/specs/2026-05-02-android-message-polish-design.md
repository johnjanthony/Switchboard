# Android Message Polish — Design Spec

**Date:** 2026-05-02
**Status:** ⏳ Designed; implementation pending.

Bundles two `docs/feature-backlog.md` items that both touch `android/app/src/main/java/io/github/johnjanthony/switchboard/ui/MessageBubble.kt`:

- **Android: streamline duplicated reply rendering** — drop the inline reply-on-question render in favor of inserting the reply chronologically-after-the-question via a new ordering field.
- **Android: per-message timestamp reveal (long-press or pull)** — render `ChannelMessage.timestamp` on demand via a horizontal-pull gesture.

---

## Problem

### Reply rendering

When John replies to an `ask_human` question, the reply text appears twice in the channel feed:

1. **Inline on the question bubble**, via `response_text` written back into the question's Firebase node by `FirebaseBackend.write_response_text` (`server/firebase.py:685`), then rendered by the `if (isAnswered && message.response_text != null)` block in `MessageBubble.kt:140-156`.
2. **As a tail "human" message**, written by `_write_history` in `server/gateway/dispatch.py:73-80` (sender `"John"`, type `"human"`).

Both writes are intentional: the inline one shows the answer attached to the question; the tail one preserves a chronological human-side entry in the feed. Together they double-render every reply on a phone screen.

### Timestamp reveal

`ChannelMessage.timestamp` (set server-side at `server/firebase.py:284`) is on the wire and parsed into the model (`android/shared/src/main/java/io/github/johnjanthony/switchboard/network/Models.kt:14`), but never rendered. Most of the time relative ordering is enough; occasionally John wants exact times to answer "how long did this reply take?" or "when did that question land?" — and there's no path to that without diving into Firebase.

---

## Goal

After this work:

- The reply text appears exactly once in the channel feed.
- That single rendering sits immediately after its corresponding question in display order — even though its real `timestamp` is later than other messages that may have arrived between the question and the reply.
- A horizontal pull on the channel feed fades absolute timestamps in over the bubbles for the duration of the pull, then fades them out on release.
- The server stops writing `response_text` to question Firebase nodes; the tail "human" message becomes the sole durable record of a reply.

---

## Non-goals

- **Snap-toggle timestamp mode.** Hold-pull only — see "Why hold-pull" below.
- **Relative timestamps** (`3m ago`, `yesterday`). Absolute only — relative adds visual weight without adding signal for the actual use cases (reply-duration math, exact-time correlation).
- **Migration of already-answered questions** in the existing Firebase database. John will clear the dev Firebase DB before deploying this change, so pre-existing question/reply pairs do not need to be tolerated.
- **Wear OS reply-rendering or timestamp gestures.** The wear app has no `MessageBubble` of its own and renders messages via the system notification surface; nothing to change here.
- **Markdown-document-viewer timestamps.** Out of scope — separate backlog item ("pinch-to-resize text") covers polish on that surface.

---

## Design

### Part 1: Reply rendering

#### Wire-format change

A new optional field `attached_to_msg_id: String | None` on `ChannelMessage` (Firebase nodes under `channels/{key}/messages/{id}`). When present, it names the msgId of the message this one logically attaches to — currently only used by tail "human" reply messages to point at their question.

#### Server changes

- **`server/firebase.py`:**
	- `write_channel_message` accepts a new optional kwarg `attached_to_msg_id: str | None = None`. When non-None, it's persisted to the message payload alongside the existing fields.
	- `write_response_text` (line 685) is removed.
- **`server/messenger.py`:** the abstract `write_channel_message` (line 55) gains the matching `attached_to_msg_id: str | None = None` keyword-only parameter so it stays in sync with the concrete signature. `write_response_text` is *not* on the abstract trait surface — it's accessed today via `hasattr(backend, "write_response_text")` capability checks at the call sites — so no other change to `messenger.py`.
- **`server/gateway/dispatch.py`:**
	- The `_spawn_bg(backend.write_response_text(...))` call at lines 55-60 (including the surrounding `if record.msg_id and hasattr(...)` guard) is deleted. The `record.msg_id` reference there now feeds only into `_write_history` below.
	- `_write_history` (lines 73-80) passes `attached_to_msg_id=record.msg_id` to `write_channel_message`.
- **`server/gateway/bulk_respond.py`:**
	- The `write_response_text` block at lines 53-54 (including the `hasattr` guard) is deleted.
	- The `write_channel_message` call at line 55 passes `attached_to_msg_id=p.msg_id`.

#### Client model

- **`android/shared/src/main/java/io/github/johnjanthony/switchboard/network/Models.kt`:** `ChannelMessage` gains
	```kotlin
	@get:PropertyName("attached_to_msg_id") @set:PropertyName("attached_to_msg_id")
	var attached_to_msg_id: String? = null,
	```

#### Client ordering

The display order is derived from a "splice" rule applied to the existing msgId-ordered message list:

> A message with `attached_to_msg_id = X` is repositioned immediately after the message with msgId `X`. If multiple messages attach to the same `X`, they appear in their original arrival order. Messages without `attached_to_msg_id` keep their msgId-ordered position.

Implementation in `android/shared/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt`:

- `addMessage` and `removeMessage` continue to maintain `channel.messages` in arrival/msgId order (the same order they have today).
- A new private helper `applySpliceOrder(messages: List<Pair<String, ChannelMessage>>): List<Pair<String, ChannelMessage>>` returns a sorted view: walk the input in order; emit each message; if any later message has `attached_to_msg_id` matching the just-emitted msgId, emit those (in arrival order) immediately after, marking them so the outer walk skips them.
- `Channel.messages` is the spliced view (we replace the field's value, not maintain two parallel lists). On every `addMessage`/`removeMessage`, recompute the spliced view from the underlying arrival-ordered store. Keep the underlying store as a separate field (`channel.rawMessages`) so the splice rule operates on an authoritative source.
- Cost: O(n) per mutation. Channel feeds are small (single-channel scrollback); fine.

#### Client UI

- **`MessageBubble.kt`:**
	- Add a constructor parameter `isAnswered: Boolean` (replaces the locally-computed `isAnswered` based on `response_text != null`).
	- Delete the `if (isAnswered && message.response_text != null) { ... }` block at lines 140-156.
	- The `RESPONDED` badge logic at lines 95-108 stays, gated on the new `isAnswered` param.
	- The `response_text` field is no longer referenced by `MessageBubble` after these deletions.
- **`SessionViewScreen.kt`:**
	- Compute a per-channel `Set<String>` of "answered question msgIds" by scanning the channel's messages for any with non-null `attached_to_msg_id`. Hoist this so it isn't recomputed per bubble in the LazyColumn loop — `remember(messages) { … }` is fine.
	- Pass `isAnswered = msg.msgId in answeredSet` into each `MessageBubble`.

#### Removing the `response_text` field

The `response_text` field is removed entirely from `ChannelMessage` (`Models.kt:13`). The Firebase DB is cleared before deploy, so no legacy-data tolerance is needed. The `RESPONDED` badge derives exclusively from the `attached_to_msg_id` cross-reference set going forward.

Preview composables that previously seeded a `response_text` value (e.g. `PreviewMessageBubbleAnsweredQuestion` at `MessageBubble.kt:230-242`) are updated to instead pass `isAnswered = true` to `MessageBubble`.

---

### Part 2: Timestamp reveal

#### Format

`ChannelMessage.timestamp` is an ISO-8601 string in UTC (set by `datetime.now(timezone.utc).isoformat()` at `server/firebase.py:284`). On the client, format as:

- **Same calendar day** (per device local time): `2:32 pm` (12-hour, no leading zero on the hour, lowercase meridiem, no seconds).
- **Different calendar day**: `2:32 pm May 2` (no year — within a few-week scrollback window, year is always implicit).

A small helper in `android/app/src/main/java/io/github/johnjanthony/switchboard/ui/` (e.g., `TimestampFormat.kt`) converts the ISO string to the display string at render time. Use `java.time.ZonedDateTime.parse(timestamp).withZoneSameInstant(ZoneId.systemDefault())` for the parse, then a `DateTimeFormatter.ofPattern("h:mm a")` (lowercased post-format) for the time, conditionally appending `MMM d` when the local date differs from today.

#### Gesture

- **Where:** `SessionViewScreen.kt`. Wrap the existing `SelectionContainer { LazyColumn { … } }` in a `Box` with a top-level `pointerInput`.
- **What:** The `pointerInput` uses `awaitPointerEventScope { … }` and:
	- On the first pointer down, starts collecting deltas without claiming the pointer.
	- Once the cumulative motion crosses an axis-resolution threshold (~10dp), commits to one axis: if `|dx| > |dy|`, claim the pointer (call `consume()`); otherwise release the pointer back to the LazyColumn so vertical scroll keeps working.
	- After claiming horizontal, accumulate horizontal magnitude and map it to a `timestampOpacity: Float` state, clamped to 0..1, with a target threshold of 80dp absolute horizontal displacement reaching opacity 1.
	- On release (pointer up or cancel), animate `timestampOpacity` back to 0 over ~150ms via `Animatable.animateTo(0f)`.
- **Direction:** Pull-left and pull-right both reveal — gesture symmetry, no preferred side.
- **State:** `timestampOpacity` lives as a `remember { mutableStateOf(0f) }` (or `Animatable(0f)`) in `SessionViewScreen` and is passed into each `MessageBubble` as a parameter.

#### Why hold-pull (not snap-toggle)

Hold-pull is mode-free: the user knows timestamps are visible because they're actively holding the gesture. Snap-toggle introduces a "did I leave it on?" question and adds a discoverability problem ("how do I get them to go away?"). The stated use cases — "when did this land?" and "how long did this reply take?" — are both single-look interactions; release-when-done matches the duration of attention.

#### UI

`MessageBubble.kt`'s sender-label `Text` (lines 63-68) becomes a `Row` spanning the bubble's effective width:

```kotlin
Row(
    modifier = Modifier
        .fillMaxWidth(0.9f)
        .padding(horizontal = 6.dp, vertical = 2.dp),
    verticalAlignment = Alignment.CenterVertically,
) {
    if (isHuman) {
        // Human-aligned: timestamp center, sender on right edge.
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            TimestampLabel(message.timestamp, alpha = timestampOpacity)
        }
        SenderLabel(message.sender)
    } else {
        // Agent-aligned: sender on left edge, timestamp center.
        SenderLabel(message.sender)
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            TimestampLabel(message.timestamp, alpha = timestampOpacity)
        }
    }
}
```

The sender stays at its alignment edge (matching today's behavior); the timestamp lives in the empty horizontal space on the same line, centered, with `alpha = timestampOpacity`. Both use `MaterialTheme.typography.labelSmall` for visual consistency.

#### SelectionContainer interaction

The top-level `pointerInput` sits *above* the `LazyColumn` but *below* the `SelectionContainer`'s text-selection layer (the SelectionContainer wraps the LazyColumn at `SessionViewScreen.kt:165`). Text selection requires a long-press first; a horizontal drag from cold state shouldn't trigger it. If a conflict surfaces during manual testing, the fallback is to lift the `pointerInput` ABOVE the `SelectionContainer` so the gesture is intercepted before selection logic sees it.

---

## Testing

### Unit tests (server)

- New focused test of `FirebaseBackend.write_channel_message` (placement: a new `tests/test_firebase_attached_msg.py` or appended to `tests/test_firebase_paths.py` based on existing fixtures): when `attached_to_msg_id` is passed, the field is persisted on the message payload; when omitted, the field is absent (we don't write explicit `null`s).
- `tests/test_gateway_ask_human.py` (specifically the dispatch test around line 740): the test backend's `write_response_text` method and the assertion-side `write_response_text` recording are deleted; the existing `channel_messages` recording is extended to assert the tail "human" entry carries `attached_to_msg_id == "m1"` (the pending's msg_id from line 742).
- `tests/test_away_mode_commands.py` (which exercises the bulk-respond path): same shape — drop `write_response_text` from its test backend; add `attached_to_msg_id` assertion to the existing `write_channel_message` recording.
- `tests/test_gateway_notify_human.py:67`: the test backend's `write_response_text` stub is deleted.
- Any other test backends that stub `write_response_text` are cleaned up in the same pass.

### Unit tests (Android)

- New unit test of the splice ordering rule in `MainViewModel`: feed a sequence of `addMessage` calls in arbitrary order, including replies-before-questions and replies-after-questions, assert resulting display order.

### UI verification

- Compose previews for `MessageBubble`:
	- Pre-existing previews (`PreviewMessageBubbleNormal`, `PreviewMessageBubblePendingQuestion`, `PreviewMessageBubbleAnsweredQuestion`, `PreviewMessageBubbleCancelledQuestion`) get an explicit `isAnswered` constructor arg matching their case; the answered preview no longer renders an inline reply.
	- New previews exercising the timestamp row at `timestampOpacity = 0f`, `0.5f`, and `1f` for both human and agent bubbles, in both same-day and different-day formats.
- Manual device verification (no automated coverage) for:
	- Horizontal-pull gesture feel — axis resolution, opacity ramp, release animation.
	- SelectionContainer co-existence — text-selection on a bubble still works after the gesture is added.
	- LazyColumn vertical-scroll co-existence — scrolling vertically through a long feed still feels native; pull doesn't fight scroll.

---

## Migration / rollout

- Server change ships with the next deploy. Old clients still receive the same Firebase data; they just see an extra new field (`attached_to_msg_id`) which their `@IgnoreExtraProperties` parser ignores.
- New client ships independently. Until both ship, the old phone client still renders the old way; the new phone client correctly handles both old and new server outputs.
- No coordinated deploy required.

---

## Open questions

None at design time. Sort-key shape, gesture style, format, and gesture direction are all locked.

---

## Files touched (summary)

**Server**

- `server/firebase.py` — extend `write_channel_message`; remove `write_response_text`
- `server/messenger.py` — add `attached_to_msg_id` keyword-only param to the abstract `write_channel_message`
- `server/gateway/dispatch.py` — drop `write_response_text` call; pass `attached_to_msg_id`
- `server/gateway/bulk_respond.py` — drop `write_response_text` call; pass `attached_to_msg_id`

**Android client**

- `android/shared/src/main/java/io/github/johnjanthony/switchboard/network/Models.kt` — new `attached_to_msg_id` field on `ChannelMessage`
- `android/shared/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt` — splice ordering helper and integration into `addMessage` / `removeMessage`
- `android/app/src/main/java/io/github/johnjanthony/switchboard/ui/MessageBubble.kt` — accept `isAnswered` param, drop inline reply block, restructure sender row to host timestamp
- `android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionViewScreen.kt` — derive `answeredSet`, host pull-gesture pointerInput, pass `timestampOpacity` and `isAnswered` per bubble
- `android/app/src/main/java/io/github/johnjanthony/switchboard/ui/TimestampFormat.kt` — new utility for ISO → display-string formatting

**Tests**

- New focused test for the `attached_to_msg_id` param on `write_channel_message` (file TBD based on existing fixtures: new `tests/test_firebase_attached_msg.py` or extension of `tests/test_firebase_paths.py`)
- `tests/test_gateway_ask_human.py` — drop `write_response_text` test stub; assert new `attached_to_msg_id` on the dispatched human message
- `tests/test_away_mode_commands.py` — same shape for the bulk-respond path
- `tests/test_gateway_notify_human.py` — drop `write_response_text` test stub
- New Android unit test for the splice ordering rule in `MainViewModel`
