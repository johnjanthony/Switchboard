# Channel Hide + Away-Mode Toggle Design

**Date:** 2026-04-24
**Status:** Approved

## 1. Problem

The current Android app treats tab closure as a single action that bundles three unrelated things: it writes `sessions/{channel_id}/state="closed"` to Firebase, auto-replies to any pending `ask_human` with a hardcoded "I'm back at my desk now…" string, and removes the channel from local UI state. The bundled reply *resembles* John saying he is back at his desk but does not actually flip the server's `away_mode_active` flag. The agent's SKILL-based pattern-match is what's doing the work. In the 2026-04-24 session that motivated this spec, the skill's context had frayed enough that the `exit_away_mode()` call was missed, so the auto-reply arrived, the agent produced terminal output, and the turn-end hook blocked it because the server flag was still set. The whole "auto-reply as back-at-desk signal" design is too subtle for the invariant it is supposed to enforce.

Separately, John needs:

- A way to remove channels from the main UI without killing them or responding to pending questions — a reversible **hide**, not a destructive **close**.
- Per-message-type control over notification loudness, so `notify_human` and relayed agent messages can be silenced without affecting `ask_human` or `send_document_human`.
- A clearly visible, hard-to-accidentally-trigger away-mode toggle in the Android app that is the *one* surface for flipping the server's global flag.
- A bulk-respond workflow on exit-away-mode that replaces today's auto-reply with an explicit, editable, one-gesture bulk reply to every pending question.

This spec replaces "close" with "hide", decouples hide from away-mode, and formalizes the toggle and bulk-respond surfaces.

## 2. Goals and Non-Goals

**Goals:**

- Replace close with a reversible hide as the only per-channel UI-management action.
- Introduce hidden-channel notification gating at the server (non-question messages on hidden channels produce no FCM push).
- Auto-unhide a hidden channel when a new `ask_human` arrives for it.
- Add an explicit away-mode toggle in the Android app that is the only way, other than the MCP tools themselves, to flip the server's `away_mode_active` flag.
- Introduce a bulk-respond editor on exit-away-mode so the old hardcoded "back at desk" text is now explicit, editable, and opt-in.
- Split the Android NotificationChannel lineup from two categories to three (questions, documents, updates) so OS-level per-category settings become authoritative for `notify_human` vs `send_document_human` vs `ask_human` behaviour.

**Non-goals (explicitly out of scope; listed again in §11):**

- Per-channel away-mode state (already on the backlog as a separate upgrade).
- Channel/session ageout and Firebase cleanup.
- The future web dashboard.
- Renaming the Firebase `sessions/` path to `channels/`.
- Per-channel or per-tool-type in-app mute controls beyond hide and OS-level channel settings.
- Custom notification sounds, LEDs, or vibration patterns.

## 3. Approach

The design rests on three orthogonal concerns, each with a distinct state surface:

| Concern | State location | Who writes | Who reads |
|---|---|---|---|
| **Hide/unhide** | Firebase `sessions/{channel_id}/hidden: bool` | Android (manual hide/unhide), Server (auto-unhide on `ask_human`) | Android listener (UI), Server FCM path (suppression) |
| **Away-mode** | Server `Registry` + `logs/away-mode.json` sidecar (source-of-truth); Firebase `/away_mode/active` (mirror) | Server only (tool calls, `/away-mode` commands, startup) | Turn-end hook (via `GET /away-mode`), Android (listener on mirror) |
| **Pending `ask_human` resolution** | Firebase `responses/{request_id}` | Android (reply, bulk reply) | Server `ask_human` await loop |

No single action touches more than one of these axes, except the explicit bulk-respond flow (which intentionally writes many `responses/` values as part of one user gesture).

## 4. Hide/Unhide Mechanism

### 4.1 Firebase schema

Add one field per channel:

```
sessions/{channel_id}/hidden: bool
```

Absent or `false` = visible. `true` = hidden.

The legacy `sessions/{channel_id}/state="closed"` field is treated as `hidden=true` on read (for both Android and the server's suppression check), for backward compatibility with channels already flagged that way by today's `closeChannel`. The `state` field is never written going forward. No Firebase data-migration is required.

### 4.2 Writers and readers

**Writers of `hidden`:**

- **Android** writes `hidden=true` when the user taps the hide icon on a channel tab.
- **Android** writes `hidden=false` when the user taps a row in the Hidden Channels dialog.
- **Server** writes `hidden=false` atomically in the FCM dispatch path when dispatching an `ask_human` question for a hidden channel (auto-unhide).

**Readers of `hidden`:**

- **Android's existing `setupChannelsListener`** filters `hidden=true` (and legacy `state="closed"`) rows out of the main `ScrollableTabRow` and into the Hidden Channels dialog dataset.
- **Server's `firebase.send_*` FCM-dispatch paths** perform a one-shot read of `hidden` immediately before firing the FCM push:
  - `message_type="question"` on a hidden channel → write `hidden=false`, then send the FCM push (auto-unhide).
  - Any other `message_type` on a hidden channel → skip the FCM push entirely. The message still lands in `sessions/{channel_id}/messages/` so it is visible if and when the channel is later unhidden.
  - Visible channel → unchanged behaviour.

The one-shot read per FCM dispatch is negligible overhead (single-digit ms against localhost's Firebase admin SDK connection) and avoids server-side state caching or listener plumbing.

### 4.3 Android UI flows

**Hide icon on each tab** — the current `Icons.Default.Close` (X) is replaced with `Icons.Default.VisibilityOff` (eye-with-slash), signalling a non-destructive action.

**Tap semantics:**

- Channel without a pending `ask_human` → tap hides immediately, no confirmation.
- Channel with a pending `ask_human` → an `AlertDialog` fires: `"<channel_id> has a pending question. Hide anyway?"` with **Hide anyway** / **Cancel** buttons. **Hide anyway** hides the channel without writing to `responses/`; the pending question remains in Firebase and the blocked agent stays blocked.

**Selected-channel fallback** — if the currently-selected channel is hidden, the UI selects the first remaining visible channel (matches today's `closeChannel` behaviour).

**Hidden Channels dialog** — opened from a new overflow menu in the TopAppBar (three-dot icon → "Hidden channels"). Renders as an `AlertDialog` with a `LazyColumn` of hidden-channel rows. Each row shows the `channel_id` plus row-level adornment that mirrors the tab-strip indicator pattern:

- Row has a red border (`MaterialTheme.colorScheme.error`) + tinted background if the channel has a pending `ask_human`.
- Row has a primary-colour border + subtler tint if the channel has any unseen activity since the last time it was visible.
- Plain row otherwise.

Additionally, a leading icon on each row reinforces the state: `Icons.Default.Help` for pending ask_human, `Icons.Default.Notifications` for unseen activity, none for quiet.

Tapping any row unhides the channel (writes `hidden=false`) and dismisses the dialog; the newly-unhidden channel becomes the selected channel.

Empty state: `"No hidden channels."` with an OK button.

### 4.4 What hide does NOT do

- No write to `responses/{request_id}` — pending `ask_human` questions stay pending.
- No change to `Registry.away_mode_active`.
- No string sent to any agent.
- No write to `sessions/{channel_id}/state` — that field is deprecated; do not touch it.

## 5. Notifications Model

### 5.1 Three Android NotificationChannels

Created in `SwitchboardFirebaseMessagingService.ensureChannels`:

| Channel ID | Importance | Backing MCP tool(s) |
|---|---|---|
| `switchboard_questions` | `IMPORTANCE_HIGH` (heads-up + sound) | `ask_human` |
| `switchboard_documents` (new) | `IMPORTANCE_DEFAULT` (status bar + sound) | `send_document_human` |
| `switchboard_updates` | `IMPORTANCE_DEFAULT` (status bar + sound) | `notify_human` + relayed `message_and_await_agent` |

`switchboard_documents` is the only new channel. `switchboard_questions` and `switchboard_updates` keep their existing IDs so any user-level OS-level importance overrides survive the upgrade.

Once an Android NotificationChannel is created, the app is no longer allowed to change its importance; only the user can, via Android Settings → Apps → Switchboard → Notifications. That is the intended surface: John silences `switchboard_updates` once on the phone and it stays silent. No in-app per-tool silencer is added.

### 5.2 Routing in `onMessageReceived`

Replace today's `isQuestion = remoteMessage.data.containsKey("request_id")` branch with a lookup on `sb_message_type` (the data-payload field the server already sets per the 2026-04-22 post-implementation note):

```kotlin
val notificationChannelId = when (remoteMessage.data["sb_message_type"]) {
    "question" -> CHANNEL_QUESTIONS
    "document" -> CHANNEL_DOCUMENTS
    "notify", "agent" -> CHANNEL_UPDATES
    else -> CHANNEL_UPDATES  // forward-compat fallback
}
```

`NotificationCompat.Builder` `setPriority` is matched to the channel importance (`PRIORITY_HIGH` for questions, `PRIORITY_DEFAULT` otherwise) so the app-level priority hint and the channel-level importance do not contradict each other.

### 5.3 Hidden-channel suppression composes with routing

The server's hide-aware FCM-dispatch rule from §4.2 fires first. When it suppresses, the client-side routing in §5.2 never runs because no FCM arrives. When it does not suppress, the FCM lands and is routed to the appropriate notification channel by message type.

## 6. Away-Mode Sync and Toggle UX

### 6.1 State ownership (unchanged)

No change to the 2026-04-23 enforcement design: the global flag lives in `Registry._away_mode_active`, persisted to `logs/away-mode.json`, read by the turn-end hook via the gateway's `GET /away-mode` endpoint. This spec only adds bidirectional sync between the server and the Android app.

### 6.2 Firebase mirror

Add a new Firebase node:

```
/away_mode/active: bool
/away_mode/updated_at: long (milliseconds since epoch)
```

The server is the only writer of this node. Android reads it to drive the pill-chip UI.

The server writes the mirror on three events:

1. **Service startup** — push the sidecar value immediately after constructing `Registry`, so Firebase reflects reality after a server outage regardless of what Android last wrote.
2. **Every `enter_away_mode()` / `exit_away_mode()` MCP tool call** — mirror the new value before returning `"ok"` to the agent.
3. **Every command-driven toggle** (see §6.3) — mirror the new value after applying it.

`updated_at` lets Android disregard mirror writes that arrive out-of-order during Firebase reconnect windows.

### 6.3 Android writes via `commands/`

The Android app never writes `/away_mode/active` directly — that node is server-owned. To request a toggle, Android pushes a new command string to `commands/{id}`, matching the existing `/spawn` pattern:

```
/away-mode on
/away-mode off
```

The existing command watcher in `SpawnHandler` (or a peer command-dispatcher if cleaner to factor during implementation) parses these verbs. On parse, it calls `registry.set_away_mode(True|False)`, writes the Firebase mirror, and logs a JSONL event (`away_mode_entered` / `away_mode_exited`) with `reason="android"`.

The round-trip is: user long-presses → Android pushes `/away-mode on|off` → server handles → server writes `/away_mode/active` → Android listener updates the pill chip. Typical latency under 200ms on a good connection.

### 6.4 Pill chip UI

A pill-shaped chip is placed in the TopAppBar actions row, immediately to the right of the Spawn `+` icon. Two states:

- **AWAY**: filled pill, `MaterialTheme.colorScheme.error` background, `Color.White` text label `AWAY`.
- **AT DESK**: outlined pill, `onSurface.copy(alpha = 0.6f)` border and text label `AT DESK`.

**Single tap does nothing** other than showing a brief `Toast`: `"Long-press to toggle"`. **Long-press** (default `combinedClickable` threshold, ~500ms) opens the confirmation dialog described in §6.5. Long-press-to-act is the primary accidental-tap guard; the confirmation dialog is the secondary guard.

### 6.5 Confirmation dialog variants

- **Off → On** (entering away mode):
  `"Enter away mode? Terminal output will be redirected to the app until you exit."`
  Buttons: **Enter** / **Cancel**.
- **On → Off with no pending `ask_human` questions across any channel (hidden or visible):**
  `"Exit away mode?"`
  Buttons: **Exit** / **Cancel**.
- **On → Off with N ≥ 1 pending questions** — the bulk-respond flow from §6.6 fires instead of a plain confirm.

### 6.6 Bulk-respond flow on On → Off

When the pill long-press fires and `pendingQuestions` (a flat scan across all channels, **including hidden channels**) is non-empty, a modal `AlertDialog` opens:

- Header: `"You have N pending question(s). Respond to all with the same message?"`
- Body:
  - An editable `OutlinedTextField` pre-filled with `"I'm back at my desk now, let's proceed in the terminal"` (the string migrated in from today's hardcoded `closeChannel` auto-reply).
  - Below the field, a non-interactive `LazyColumn` listing each pending channel's `channel_id` plus a one-line preview of the question (truncated to ~80 chars) so the user sees what they are about to bulk-answer.
- Buttons (three):
  - **Send to all** — for each pending question, write the edited text to `responses/{request_id}`, then clear the entries locally from `_pendingQuestions`, then push `/away-mode off` to `commands/`. Hidden channels *remain hidden* after the bulk reply — there is no auto-unhide for bulk responses; the user already decided they were done with them.
  - **Skip (toggle off only)** — push `/away-mode off`; no `responses/` writes. Pending asks stay pending.
  - **Cancel** — abort; no command, no responses, away-mode stays ON.

### 6.7 Failure modes

- **Firebase unreachable from the server at startup** — the turn-end hook still functions (reads via gateway HTTP, not Firebase). Tool-call toggles still persist to sidecar and update in-memory. The mirror update is skipped silently; the next successful `set_away_mode` call (tool or command) re-pushes the mirror. No separate health-check loop is added in V1.
- **Android offline during a toggle** — Firebase's client-side offline cache queues the `commands/` write and the mirror subscription. Pill chip shows optimistic state; correct state arrives when the connection resumes.
- **Race: user toggles OFF while an agent tool call is flipping ON** — the toggle OFF goes through `commands/` and is processed sequentially by the server. Whichever write lands last in Firebase wins, and the mirror reflects the resolved value. Single-user system; last-writer-wins is acceptable.

## 7. Decoupling from Current `closeChannel`

**Today's `closeChannel` in `MainViewModel.kt:205`** does three bundled things:

1. Writes `sessions/{channel_id}/state="closed"`.
2. Auto-replies to pending `ask_human` with a hardcoded string.
3. Removes the channel from local state.

**The new `hideChannel`** writes `sessions/{channel_id}/hidden=true` and nothing else; the Firebase listener moves the row to the Hidden Channels dialog on its own. No write to `responses/`. No change to `Registry.away_mode_active`. No string sent to any agent.

**The hardcoded "I'm back at my desk now, let's proceed in the terminal" string** moves from inside `closeChannel` to the default text of the bulk-respond editable field in §6.6. Same wording, but only sent when the user explicitly confirms bulk-respond, and editable first.

**Agent-side SKILL.md is unchanged.** Agents do not interact with hide state. An agent blocked on `ask_human` on a hidden channel stays blocked; its next ask_human (on timeout retry or next turn) auto-unhides the channel and is pushed to John normally.

## 8. Server Changes (file-level)

### `server/firebase.py`

- FCM dispatch wrappers (the `send_question` / `send_notification` / `send_document` equivalents, already unified under `write_channel_message` per 2026-04-22) gain a pre-push `hidden` lookup:
  - Read `sessions/{channel_id}/hidden`.
  - If `true` and `message_type == "question"`: write `hidden=false`, then send FCM.
  - If `true` and `message_type != "question"`: skip FCM (message write still happens upstream in the normal path).
  - If `false` or absent: send FCM normally.
- New method `write_away_mode_mirror(active: bool)` writing `/away_mode/{active, updated_at}`.

### `server/registry.py`

- `set_away_mode(active)` gains an optional post-set callback. After updating in-memory state and persisting the sidecar, the callback (if registered) is invoked with the new value. `main.py` registers a callback at startup that invokes `backend.write_away_mode_mirror(active)`. This keeps `Registry` free of direct backend coupling, consistent with its current role as a pure in-memory data holder.

### `server/spawn.py`

- The existing `/spawn` command parser is extended to also recognize `/away-mode on` and `/away-mode off`. Handler calls `registry.set_away_mode(True|False)` and emits the corresponding JSONL audit event with `reason="android"`.
- Unknown subcommands under `/away-mode` (e.g. `/away-mode wobble`) are logged and ignored; they never raise or block the command watcher.
- If `spawn.py` grows unwieldy from hosting both command families, a follow-up refactor can extract a shared command-dispatcher. That refactor is out of scope for this spec.

### `server/main.py`

- On startup, after constructing `Registry`, invoke `backend.write_away_mode_mirror(registry.is_away_mode_active())` to push the authoritative sidecar value to Firebase.

### `server/logging_jsonl.py`

- Existing `away_mode_entered` / `away_mode_exited` event types accept an optional `reason` string with values `"tool"`, `"spawn"`, or `"android"`.

### `server/messenger.py` (ABC)

- Add `write_away_mode_mirror(active: bool)` to the `MessengerBackend` ABC; implement in `android.py` and any test mocks.

## 9. Android Changes (file-level)

### `android/app/src/main/java/io/github/johnjanthony/switchboard/network/ApiService.kt`

- `Channel` data class gains a `hidden: Boolean = false` field.

### `android/app/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt`

- `setupChannelsListener` reads `hidden` (and legacy `state`) on add/change; filters hidden rows out of the main `_channels` state and into a new `_hiddenChannels` `StateFlow<Map<String, Channel>>`.
- New listener on `/away_mode/active` maintains `_awayModeActive: StateFlow<Boolean>`.
- New functions:
  - `hideChannel(channelId)` — writes `sessions/{channelId}/hidden = true`.
  - `unhideChannel(channelId)` — writes `sessions/{channelId}/hidden = false`.
  - `requestAwayModeToggle(desired: Boolean)` — pushes `/away-mode on` or `/away-mode off` to `commands/`.
  - `bulkRespondAndExit(text: String)` — iterates `pendingQuestions` (including for hidden channels), writes each `responses/{requestId}`, clears pending locally, then pushes `/away-mode off`.
- `closeChannel` is removed.

### `android/app/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt`

- Tab close `IconButton` icon changed from `Icons.Default.Close` to `Icons.Default.VisibilityOff`; `onClick` renamed/repointed at the hide path.
- Existing "Close Session" confirmation dialog is removed for clean hides. Replaced by a new "Pending question — hide anyway?" dialog shown only when the target has a pending `ask_human`.
- TopAppBar gains an overflow `IconButton` opening a "Hidden Channels" `AlertDialog` rendered from `hiddenChannels` state.
- TopAppBar gains the AWAY/AT DESK pill chip bound to `awayModeActive`. `combinedClickable`: long-press opens the confirmation or bulk-respond dialog per §6.5/§6.6; tap shows a Toast.
- New `BulkRespondDialog` composable bound to `pendingQuestions` and pre-filled with the default text from §6.6.

### `android/app/src/main/java/io/github/johnjanthony/switchboard/fcm/SwitchboardFirebaseMessagingService.kt`

- `CHANNEL_DOCUMENTS = "switchboard_documents"` added as a companion constant.
- `ensureChannels` creates the three channels per §5.1.
- `onMessageReceived` routes on `sb_message_type` per §5.2.

## 10. Tests

### Server

| File | What it covers |
|---|---|
| `tests/test_firebase.py` (extend or new) | FCM dispatch reads `hidden`; hidden + non-question → skip push; hidden + question → clear `hidden` then push; not-hidden → push normally |
| `tests/test_spawn.py` (extend) | `/away-mode on` / `/away-mode off` command parsing; handler flips `Registry.away_mode_active`; writes audit event with `reason="android"`; unknown `/away-mode` subcommands do not crash |
| `tests/test_registry.py` (extend) | `set_away_mode` triggers the Firebase mirror callback (mocked) in addition to sidecar persistence |
| `tests/test_main_routes.py` (extend) | Mirror is pushed on startup with the sidecar's value |

### Android

Unit tests for `MainViewModel`:

- `hideChannel` writes `hidden=true`; does not touch `responses/`.
- `unhideChannel` writes `hidden=false`.
- `bulkRespondAndExit` writes `responses/{requestId}` for each pending question across visible and hidden channels, clears them locally, then pushes the `/away-mode off` command.
- Legacy `state="closed"` channels appear in `_hiddenChannels` and can be unhidden.
- Setting up listeners twice is idempotent.

Compose UI tests (if harness available, else manual):

- Pill-chip tap shows Toast, does not toggle.
- Pill-chip long-press opens the correct dialog variant per away-mode state and pending-question count.
- Hidden Channels dialog renders adornment correctly for each of the three row states.

## 11. Out of Scope

Restated for anchoring future decisions:

- Per-channel away-mode tracking (existing backlog item, layers cleanly on top of this design).
- Channel / session ageout and Firebase cleanup (separate backlog task).
- Web dashboard.
- Renaming Firebase `sessions/` path to `channels/`.
- Per-channel mute finer than hide.
- Custom notification sounds, LEDs, vibration.
- In-app per-tool-type importance override (OS-level channel settings are authoritative).
- Changes to pending-`ask_human` timeout behaviour.
- Hidden-channel count badge on the TopAppBar.
- Data-migration pass for existing `state="closed"` Firebase entries (read-only handling is sufficient).
