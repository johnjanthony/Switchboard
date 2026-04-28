# Switchboard Feature Backlog

Open/proposed features for Switchboard, grouped by where the work lives. Shipped items have been moved to [`../PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md). When an item here is picked up, it gets its own spec + plan per the existing workflow.

---

# Server

## Log rotation

`logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).

---

## `ask_human` rate limiting

Per-channel token bucket on `notify_human` and `send_document_human` shipped 2026-04-23; `ask_human` is not yet rate-limited. Low priority — `ask_human` is self-paced by the human reply, unlike fire-and-forget notifications.

---

## Database ageout sweep

Periodically clean up old questions, responses, and documents from Firebase (e.g., delete entries older than 30 days). This prevents the Realtime Database and Storage from growing indefinitely and keeps the Android app's history retrieval performant.

---

# Client

## Web Dashboard for Conversation Monitoring & Interaction

**Proposed 2026-04-23.** A desktop-based web interface to supplement the Android app, allowing for more comfortable long-form replies and better visibility into multiple simultaneous sessions.

**Key Features:**

- **Real-time Monitoring**: Stream all active sessions from Firebase Realtime Database with a multi-pane or tabbed view.
- **Full Interaction**: Mirror the Android app's interactive capabilities:
  - Reply to `ask_human` prompts (including suggestion button support).
  - Inject messages into collaborative channels (`message_and_await_agent`).
- **Session Management**: View historical (closed) sessions and audit logs.
- **Visual Cues**: High-visibility indicators for pending questions and unseen activity, synced with the Android app's state.

**Technical Approach:**

- **Frontend**: A lightweight Single Page App (React, Vue, or vanilla JS) using the Firebase Web SDK for direct RTDB binding.
- **Deployment**: Can be hosted via Firebase Hosting for remote access or served locally by the Switchboard server (e.g., via FastAPI static files) for a "local-first" experience.

---

## Android: swipe gestures on channel rows

Add directional swipe actions to `SessionRowComposable` on Page A:
- **Swipe left** → hide the channel (equivalent to `viewModel.hideChannel(cwdKey)`).
- **Swipe right** → exit away mode for that channel (equivalent to `viewModel.requestAwayModeToggle(cwdKey, false)`, setting the per-cwd override to at-desk).

Both should reveal a colored action affordance during the swipe (red for hide, green/blue for at-desk) and commit on full swipe / snap back if released early — Material 3 `SwipeToDismissBox` or similar pattern. Long-press / context menu / TabInfoPopover access continues to work for the no-swipe path.

---

## Android: suggestion buttons as notification actions

When `ask_human` is called with suggestions, render them as tappable action buttons on the notification banner so the developer can reply without opening the app.

**What it takes:**

- **Server (`firebase.py`)** — include suggestions as a JSON-encoded string in the FCM data payload alongside `request_id` and `channel_id`
- **New `NotificationReplyReceiver`** — a `BroadcastReceiver` that fires silently when an action button is tapped, writes the answer directly to Firebase `responses/{request_id}`, and dismisses the notification
- **FCM service** — parse suggestions from data payload, add up to 3 `addAction()` calls to the notification builder
- **Manifest** — register the receiver

**Constraint:** action buttons appear on the *expanded* notification, not the collapsed heads-up banner — the user swipes down on the banner to reveal them. Still faster than opening the app.

---

## Android: copy message text to clipboard

Allow the user to copy message contents from the channel view — both whole-message copy and partial text-selection copy — for any message type (agent updates, `ask_human` prompts, replies, system messages, collab injections, document captions, etc.).

**What it takes:**

- **Whole-message copy.** Long-press on a message bubble surfaces a context action (Material 3 dropdown menu or bottom sheet) with "Copy". Writes the full rendered text to `ClipboardManager` and shows a brief toast/snackbar confirmation. Should work uniformly across all message types — pull the source text from the `ChannelMessage` body rather than the rendered Compose tree.
- **Partial selection copy.** Enable text selection inside message bubbles so the user can drag-select a span and copy via the standard Android selection toolbar. In Compose this means swapping the message body `Text(...)` for `SelectionContainer { Text(...) }` (or putting the whole message list inside one `SelectionContainer`). Markdown-rendered bubbles need the same treatment — verify the markdown renderer's output is selectable.
- **Markdown messages.** When a message is rendered as markdown, the copied text should be the *plain* text (what the user sees), not the raw markdown source. If both are useful, the long-press menu can offer "Copy text" and "Copy as markdown" as separate actions.
- **No new server work.** Pure client-side feature.

Low-medium priority — improves quality-of-life for grabbing snippets out of agent output (paths, error messages, code) without retyping.

---

## Android: investigate MALFORMED MESSAGE deserialization warnings

**Surfaced 2026-04-27** while debugging spawn-collision. `MainViewModel.kt` lines 175-198 log `MALFORMED MESSAGE at channels/<key>/messages/<id>` with `Value Type: java.lang.String, Value Content: <empty>` when `getValue(ChannelMessage::class.java)` throws. Direct Firebase admin queries against the same paths return correctly-shaped dicts, so the data IS dict-shaped at rest — the phone's listener appears to fire on a transient state where `snap.value` is a String. The catch swallows the error, so it's log noise plus an occasional missed render rather than data loss. Low priority — investigate whether it's a Firebase SDK race, a partial-write listener fire, or something else, and either suppress the log noise or fix the deserialization path.

---

# Combined (server + client)

## Withdraw pending questions when an unattended agent dies

**Surfaced 2026-04-26** during cwd-as-channel post-merge testing (test 6d). The common kill-and-respawn case is now covered by **cancel-on-spawn** (shipped 2026-04-27): `Registry.cancel_pending_for_cwd` + `SpawnHandler._cancel_prior_pending` write the WITHDRAWN marker via `mark_question_cancelled` before the new agent launches. Closes the typical dev workflow.

**Still open** — agents that die *without* a respawn (unattended long-running crash) still leave their pending questions hanging until the 24h timeout fires `send_timeout_followup` rather than `mark_question_cancelled`. Two complementary approaches remain as future-insurance:

1. **HTTP keepalive disconnect detection.** Investigate whether MCP's streamable-HTTP transport surfaces a disconnect signal to in-flight tool handlers (FastMCP / `streamable_http.py`). If yes, plumb that into a CancelledError on the awaiting future. May not be reachable depending on the MCP SDK.
2. **Agent liveness pings.** Heartbeat protocol: agents send periodic keepalives; missing two in a row = mark all their pending questions cancelled. More moving parts, more state, but transport-agnostic.

Both are insurance against unattended crashes; the common case is handled.

---

## Away-mode Firebase schema reorganization

**Proposed 2026-04-26**, surfaced during cwd-as-channel post-merge testing. Two related schema changes that the current `away_mode/{global, overrides/{cwdKey}}` shape made awkward:

1. **Co-locate per-channel away-mode with the channel.** Move `away_mode/overrides/{cwdKey}` → `channels/{cwdKey}/away_mode`. The override conceptually belongs to the channel; co-locating gets lifecycle alignment for free (deleting/wiping a channel removes its override too, no orphan-on-channel-delete bug). Phone-side it removes one Firebase listener — the channel listener already covers it.

2. **Group global settings.** Move `away_mode/global` → `global_settings/away_mode`. `global_settings/` becomes the home for any future top-level switches (notification quiet hours, default sender, etc.); today it has one tenant. Once both moves land, the `away_mode/` node can be deleted entirely.

3. **Cross-device unseen state synchronization.** Move `unread_count` and `unseen` flags into the Firebase `channels/{key}/` node. Update `MainViewModel` to write to these nodes when a channel is selected. Update both apps to observe these remote values instead of local state. This ensures that reading a message on the phone correctly clears the indicator on the watch.

**What it takes:**

- **Server (`firebase.py`)** — rewrite `write_away_mode_mirror` to target `global_settings/away_mode` for global, `channels/{cwdKey}/away_mode` for per-channel. The "remove override" path becomes `db.reference(f'channels/{key}/away_mode').delete()`. Bulk clear on global-toggle becomes one Firebase multi-location update (`db.reference().update({...})`) walking `registry.cwd_overrides()`.
- **Android (`MainViewModel`)** — drop the `setupAwayModeListener` override-listener; pull `away_mode` from the existing channel snapshot in `syncChannel`; add a separate listener on `global_settings/away_mode`. The `Channel` data class gains an `awayMode: Boolean? = null` field (null = follow global).
- **Server `Registry`** — internal in-memory representation can stay as-is (`_global_away` + `_cwd_overrides`); only the mirror shape changes. `away-mode.json` sidecar likewise unchanged.
- **Migration** — clean Firebase wipe on deploy. Stale `away_mode/*` paths can be left to die; or do a one-shot delete at startup.
- **Tests** — `test_messenger_contract` signature checks unaffected (signature unchanged). Any test that asserts specific Firebase paths needs updating.

**Why now is a follow-up, not part of cwd-as-channel:** orthogonal to spawn-flow correctness; the current 3-fix bundle (spawn cwd-override, spawn channel routing, mirror-cleared-overrides) leaves the system *correct* under the existing schema. Schema reshape is a separate, focused branch.

---

## Away-Mode Framing Check

Add an automated check to ensure that every agent response in away mode starts with a tool call. Server-side enforcement (gateway/Stop hook) plus skill-doc reinforcement on the agent side.

---

## Skill Instruction Polish

Periodically review and harden `SKILL.md` based on failure patterns (e.g., the 2026-04-23 terminal leak incident). Edit only the in-repo `skill/SKILL.md` — the user-level installs at `~/.claude/skills/switchboard/` and `~/.gemini/skills/switchboard/` are symlinks to it, so changes flow through automatically.

---

## Multi-Surface Voice & Summary Integration

**Proposed 2026-04-25.** Major multi-surface UX initiative spanning phone, Wear OS, and Android Auto. Introduces a Firebase Cloud Function (Gen 2) that uses Gemini 3.0 Flash to transform raw agent updates into a `display_metadata` object — surface-specific strings tuned for each device (`summary_phone`, `glance_wrist`, `speech_payload`, `progress_state`). Surfaces consume the metadata via `Notification.ProgressStyle` + `MessagingStyle`, with TTS read-aloud, RemoteInput voice-reply, an Android Auto messaging bridge, and a remote-session kill switch. Includes smart-throttling, offline cache for the last N speech payloads, and cross-surface notification cancel-sync.

Requires Firebase Blaze plan upgrade (Cloud Functions Gen 2). Spec breaks into 10 work items SB-01 through SB-10.

**Spec:** [`docs/Multi-Surface Voice and Summary Integration.md`](Multi-Surface%20Voice%20and%20Summary%20Integration.md).

---

## Timeout snooze via Android app

Add a "Snooze" button to the `ask_human` notification/tab that extends the window by 2h. Implementation: Android app writes `snooze: true` to the question object; gateway intercepts the change and resets the wait clock in the registry.

---

# Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** Legacy Telegram concept, no longer applicable.
- **Multi-user chat support.** Single-developer model is baked into the spec. Don't touch until there's a concrete second user.
- **MarkdownV2** — Telegram flavour. Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message. Obsolete after Telegram removal.
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
