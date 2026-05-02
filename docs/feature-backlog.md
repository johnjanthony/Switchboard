# Switchboard Feature Backlog

Open/proposed features for Switchboard, grouped by where the work lives. Shipped items have been moved to [`../PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md). When an item here is picked up, it gets its own spec + plan per the existing workflow.

---

## Server

### Persistence layer (Firebase hybrid write-behind)

**Surfaced 2026-04-28** in the codebase review (`docs/2026-04-28-codebase-review.md` H1). Deferred at John's call.

**Problem.** `Registry` (`server/registry.py:34-46`) stores pending `ask_human` futures and collab sessions in plain `dict`s. On service restart, every pending question dies (waiting agents block until the 24h timeout) and every collab session evaporates (both partners' `message_and_await_agent` calls die likewise). The current mitigation is the `collab-sessions.json` sidecar (`server/main.py:46-66`) which only writes a "session lost" notice — it does not restore. `AGENTS.md` already documents the operational constraint *"Never restart the service while a collab session is active."*

**Target architecture: Firebase hybrid write-behind** (not JSON, not SQLite — John's preference):

- **Primary state stays in memory.** `Registry._pending` and `Registry._sessions` continue to be the authoritative state for the hot path; mutations are O(1) dict ops with no network latency.
- **Firebase as async backup.** Every mutation schedules a write-behind task to Firebase RTDB nodes (e.g., `pending_requests/{request_id}`, `collab_sessions/{cwd}`). Use the bg-task tracking pattern from H3 so writes can't be GC'd.
- **Startup seed.** Read both nodes at startup, hydrate `Registry`, re-create futures (any future that was already-resolved on disk converts to a "next message delivers" semantic), broadcast a "we restarted; resume" status to each affected channel.
- **Tradeoff accepted.** A crash between in-memory mutation and Firebase flush loses a few seconds of state. Acceptable in exchange for keeping `ask_human` latency unchanged (firebase_admin is sync, wrapped in `asyncio.to_thread` — sync writes on the hot path would add ~100ms per call).

**Extends the already-shipped Firebase schema** (per-channel `away_mode`, `unread_count`, `pending_responses`, etc., all under `channels/{key}/`). Pending-requests-as-Firebase-nodes follows the same co-location pattern.

**What it takes:**

- New module `server/persistence.py` (or similar) — write-behind queue + flush coroutine + reload helpers.
- `Registry.add` / `resolve` / `remove` / `add_session` / `remove_session` fire write-behind hooks.
- Startup path in `server/main.py:_run` reads + seeds before listeners start.
- Tests for crash-recovery scenarios and the "future was already resolved" edge case.
- Document in `AGENTS.md` that the restart-during-collab constraint is now relaxed (still not free — a few-second mutation window is at risk).

---

### Replace `firebase_admin.db.listen()` with own SSE consumer (M1 fallback)

**Surfaced 2026-05-01** in `docs/superpowers/specs/2026-05-01-listener-supervision-and-healthz-design.md` Q4.

**Problem.** `SupervisedListener` detects SDK-thread death via `registration._thread.is_alive()` — a leading-underscore attribute on `firebase_admin.db.ListenerRegistration`. The check works because the SDK assigns `self._thread = threading.Thread(...)` in `ListenerRegistration.__init__`. If a future firebase_admin upgrade renames the attribute, the supervisor's `getattr(reg, "_thread", None)` fallback returns `None`, the registration is treated as alive, and we silently lose death detection again.

**Trigger to pick up.** firebase_admin renames `_thread`, restructures `ListenerRegistration`, or otherwise breaks the liveness check. Symptom: `SupervisedListener.crash_count` stays at 0 across a known network outage, OR a future firebase_admin pin reveals the AttributeError fallback in tests.

**Target fix.** Replace `db.reference(path).listen(callback)` calls with our own SSE consumer built on `firebase_admin._sseclient.SSEClient` (the lower-level primitive). We control the SSE iteration loop, the try/except, and the reconnect — no reliance on private SDK attributes for liveness.

**Effort estimate.** ~1 day. Larger surface than M1 (the listener machinery becomes ours rather than the SDK's), but the supervision/health-reporting interface from M1 stays identical so the consumer migration is internal.

---

### Collab session garbage collection

**Surfaced 2026-05-01** in `docs/superpowers/specs/2026-05-01-listener-supervision-and-healthz-design.md` Q5.

**Problem.** Collab sessions are not actively garbage-collected. The 2026-04-23 BYO design explicitly accepted this (`docs/superpowers/specs/2026-04-23-bring-your-own-session-design.md` line 59 — "No explicit session teardown") at single-developer scale. Since listener supervision shipped (M1, 2026-05-01), each active collab session has its own supervised inject listener. **Wrinkle:** the supervisor outlives the listener registration if the session is purged without explicit teardown, leaving an idle supervisor task running until service shutdown. Today the leak is harmless (idle asyncio task); future work that GCs sessions must remember to call `await sup.stop()` for the inject supervisor as part of teardown.

**Target fix.** Active session GC — a background task that periodically scans `Registry._sessions` for idle sessions (no `_waiting`, no recent `deliver`, no recent `enroll`) older than some threshold and removes them. Teardown calls `await self._supervised[f"inject:{session_id}"].stop()` before removing the session from the registry, plus the existing Firebase metadata cleanup.

**Trigger to pick up.** Real friction from accumulated sessions (e.g. `/healthz` showing dozens of idle `inject:*` listeners after a few weeks of uptime) OR memory growth that traces to long-lived `CollabSession` objects.

**Effort estimate.** ~half day, including tests. Idle threshold is the main design call.

---

### Non-blocking partner messaging — *tentative; needs design pass* (collab protocol enhancement)

**Surfaced 2026-04-29** in conversation while reviewing the 2026-04-28 sweep. **Not greenlit** — added here so it isn't lost, but a real design pass is needed before implementation. Wait until the H8/H9/H10 fixes have been shaken out in production use (i.e. don't bolt this on while the recent collab-protocol work is still settling).

**Use case the enhancement would address.**

Today every agent-to-agent message goes through `message_and_await_agent`, which both sends and blocks the caller awaiting a reply. That forces strict turn-taking: one agent is always live (running tool calls), the other always blocked. Three patterns are currently impossible or awkward as a result:

1. **Status pings during long work.** An agent 10+ minutes deep in a task has no way to tell its partner "still alive, ~5 minutes remaining" without burning a turn — sending blocks the live agent for the duration of the partner's reply.
2. **FYI / no-reply context drops.** "Heads up, I'm restarting T5 because of the test failure I just hit" is meaningful for the partner but doesn't demand a reply. The blocking model overstates what's being asked.
3. **Asymmetric pacing.** Either agent should be free to work, listen, or be idle independently — not lockstep-alternate.

**Sketches of what a fix could look like (none endorsed; design pass required).**

- **Variant A: generic `message_agent(cwd, sender, message)` — fire-and-forget.** Sends the message (resolves the partner's `_waiting` future if present, else buffers in `_pending`) and returns immediately. Maximally flexible; enables all three patterns above.
- **Variant B: narrower `notify_partner(...)` — FYI semantics only.** Same wire format as Variant A but named to signal "no reply expected." SKILL rule becomes: use `notify_partner` for status / context drops only; use `message_and_await_agent` whenever the message demands action. Narrower contract = smaller failure surface.

**Failure modes any variant must address.**

The H8/H9/H10 sweep relies on the invariant that *someone is always either running tool calls or sitting in `_waiting`* — that's what makes the H9 turn-end hook tractable ("don't silently exit while partner is blocked"). A non-blocking send breaks that:

- Agent A sends `message_agent("ready")`, ends turn.
- Agent B is also idle (ended turn earlier).
- A's message sits in `_pending[B]` forever. No `_waiting` future ⇒ no H8/H9 trigger; the conversation dies silently.

A new guard would be needed alongside the new tool. Possible shapes:

- **Server-side unread tracking + new `/collab-unread?cwd=...&sender=...` route**, hook blocks turn-end if you have queued messages from your partner. Same architectural pattern as H9.
- **Explicit lifecycle states** (`live`, `listening`, `idle`) with the gateway tracking which agent is live; mismatches surface to John.
- **Strict pairing rule**: every `message_agent` call must be followed by either another `message_agent`, a `message_and_await_agent`, or an `end_collab` before turn-end. Hook enforces.

**Open questions for the design pass.**

- Is the use case (status pings during long work) common enough to justify a new tool, or rare enough to live with the existing pattern + brief intermediate `message_and_await_agent` calls?
- Variant A vs B vs something else?
- Does this combine well with H10 coalescing, or do we need a coalescing-aware delivery flag?
- What's the SKILL.md guidance for *when* an agent should reach for `message_agent` vs `message_and_await_agent`?

**Trigger to pick this up.** Real friction in production from the long-running status-ping case (i.e. multiple times catching ourselves wanting to send-without-block). Don't pre-build for hypothetical future patterns.

---

### Log rotation

`logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).

---

### `ask_human` rate limiting

Per-channel token bucket on `notify_human` and `send_document_human` shipped 2026-04-23; `ask_human` is not yet rate-limited. Low priority — `ask_human` is self-paced by the human reply, unlike fire-and-forget notifications.

---

### Database ageout sweep

Periodically clean up old questions, responses, and documents from Firebase (e.g., delete entries older than 30 days). This prevents the Realtime Database and Storage from growing indefinitely and keeps the Android app's history retrieval performant.

---

## Client

### Web Dashboard for Conversation Monitoring & Interaction

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

### Android: suggestion buttons as notification actions

When `ask_human` is called with suggestions, render them as tappable action buttons on the notification banner so the developer can reply without opening the app.

**What it takes:**

- **Server (`firebase.py`)** — include suggestions as a JSON-encoded string in the FCM data payload alongside `request_id` and `channel_id`
- **New `NotificationReplyReceiver`** — a `BroadcastReceiver` that fires silently when an action button is tapped, writes the answer directly to Firebase `responses/{request_id}`, and dismisses the notification
- **FCM service** — parse suggestions from data payload, add up to 3 `addAction()` calls to the notification builder
- **Manifest** — register the receiver

**Constraint:** action buttons appear on the *expanded* notification, not the collapsed heads-up banner — the user swipes down on the banner to reveal them. Still faster than opening the app.

---

### Android: pinch-to-resize text in message and document viewers

**Surfaced 2026-05-02.**

**Problem.** Code blocks, log snippets, and full-document Markdown can be hard to read at the default font size on a phone — particularly for diffs and structured tables. No path today other than the system magnifier.

**Target behavior.** Pinch gesture **adjusts the font size of the rendered text** (not a pixel-level canvas zoom). Text reflows at the new size, so no panning is needed — the existing vertical scroll continues to do the right thing. Applies in:

- **Message bubbles** in the channel feed (`MessageBubble.kt`) — bump up the type for a code-heavy reply without leaving the chat.
- **Markdown document viewer** (`MarkdownViewerScreen.kt`) — comfortable reading size for long documents shared via `send_document_human`.

**What it takes:**

- Compose `pointerInput` with `detectTransformGestures` to capture pinch deltas; map the scale factor onto the rendered content's `fontSize` / `LocalDensity` instead of a graphic-layer `scale`.
- Min/max bound (e.g. 0.75x–2.5x) with sensible step granularity so pinches don't feel runaway.
- Decide scope of the new size: persists per-channel? per-app session? resets on close? (Persisting per-app makes most sense — accessibility-style preference rather than a transient gesture state.)

**Trigger to pick up.** Already friction-driving for code-heavy replies or shared documents. Bundle with the timestamp / reply-render polish.

---

### Android: investigate MALFORMED MESSAGE deserialization warnings

**Surfaced 2026-04-27** while debugging spawn-collision. `MainViewModel.kt` lines 175-198 log `MALFORMED MESSAGE at channels/<key>/messages/<id>` with `Value Type: java.lang.String, Value Content: <empty>` when `getValue(ChannelMessage::class.java)` throws. Direct Firebase admin queries against the same paths return correctly-shaped dicts, so the data IS dict-shaped at rest — the phone's listener appears to fire on a transient state where `snap.value` is a String. The catch swallows the error, so it's log noise plus an occasional missed render rather than data loss. Low priority — investigate whether it's a Firebase SDK race, a partial-write listener fire, or something else, and either suppress the log noise or fix the deserialization path.

---

### Wear OS: notification tap should open the watch app on the relevant channel

**Surfaced 2026-05-01.** The watch app's notifications are built in `SwitchboardFirebaseMessagingService.showNotification` (`android/wear/src/main/java/io/github/johnjanthony/switchboard/fcm/SwitchboardFirebaseMessagingService.kt:75`) with `NotificationCompat.BigTextStyle` and a `setContentIntent` carrying `EXTRA_AGENT_ID` / `EXTRA_MESSAGE_ID`. In practice they render as plain Wear OS notifications — the affordance to drop into the Switchboard wear app from the notification is not obvious, and the surface feels like a default-platform notification rather than part of the app.

**Goal.** Tapping a notification on the watch reliably opens the Switchboard wear app and lands on the channel (and ideally the specific message) the notification was for. The deep-link plumbing already exists in `MainActivity.handleNotificationIntent` (`android/wear/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt:81`) — the gap is in making the notification itself invite that interaction.

**Explicitly out of scope (for now):** Suggestion-button-as-notification-action behavior. The phone equivalent is tracked separately under [Android: suggestion buttons as notification actions](#android-suggestion-buttons-as-notification-actions). Whether that pattern is even the right shape for a watch (versus opening the app and using the existing reply flow) is a question for the spec pass on this item, not an assumed yes.

**Likely investigation areas — pick the cheapest path that meets the goal:**

- Whether `Notification.WearableExtender` / `setContentAction` provides a more obvious "open in app" affordance than the bare `setContentIntent` we use today.
- Whether watch-only `MessagingStyle` (a sub-feature of [Multi-Surface Voice & Summary Integration](Multi-Surface%20Voice%20and%20Summary%20Integration.md) SB-04) gets us most of the way without pulling in the Cloud Function pieces.
- Whether the wear app would benefit from an `OngoingActivity` (SB-03) for actively-pending questions so they persist on the watch face rather than living only in the notification stream.

**Trigger to pick up.** Real friction during away-mode watch use — i.e. John finding himself wishing a tap on a watch notification just dropped him into the relevant channel.

---

### Android Auto: messaging-style integration for `ask_human` (with optional voice spawn)

**Surfaced 2026-05-01.** A standalone Android Auto application that surfaces Switchboard `ask_human` traffic the way Auto's default messaging-app integration does — see and hear pending questions while driving, respond verbally, hands-free.

**Core capabilities.**

- **Inbound.** Pending `ask_human` questions land in the Auto messaging surface. The car reads the question (or a summary) aloud through the system's messaging-app TTS path — same UX shape as receiving a text message.
- **Outbound.** John responds by voice; Auto's STT transcribes and routes the reply back through the existing `responses/{request_id}` Firebase write path. End-state is identical to a watch or phone reply, so server-side this should be free.
- **Driver-distraction compliance.** Use system-managed `MessagingStyle` + `RemoteInput` with `SEMANTIC_ACTION_REPLY`. No custom UI — Android Auto's DD review rejects anything bespoke.

**Stretch capability — likely yes, but parked until the messaging side ships.** Voice-command session spawn: "Hey Switchboard, start a session in `<project>` to `<task>`" hits the existing `server/spawn.py` pipeline. Open questions for the spec pass:

- Where does the voice intent originate — Auto's App Actions, a Google Assistant integration, or a Switchboard-side mapping of pre-configured voice phrases to spawn calls?
- How does John pick a `cwd` hands-free? Almost certainly via a small set of named "favorites" (e.g. `Switchboard`, `RPDM next-gen`) rather than spelling out paths.
- What's the confirmation step before a session actually starts? Voice spawn is moderately destructive (costs API tokens, creates external work, may collide with `_SPAWN_COLLISION_TIMEOUT_SECONDS` rules in `server/spawn.py`) — likely needs a verbal "yes" before launch.

**Relationship to existing backlog.** This is the concrete Auto-only pickup of [Multi-Surface Voice & Summary Integration](Multi-Surface%20Voice%20and%20Summary%20Integration.md) items SB-04 (`MessagingStyle`), SB-05 (TTS), SB-06 (`RemoteInput` STT), SB-07 (Auto Messaging Bridge), and SB-08 (Remote Session Kill Switch), scoped down to one surface and without a hard dependency on the `display_metadata` Cloud Function (SB-01) — v1 can pass through raw question text with a server-side length cap.

**Trigger to pick up.** Explicit prioritization. Unlike most backlog items this won't surface as passive friction — John has to want hands-free Switchboard in the car badly enough to commit to a multi-day implementation across a new client surface.

---

### Android: App Actions for home Google Assistant voice use

**Surfaced 2026-05-01** while talking through home Google Assistant integration options. Background: the obvious "voice app on Nest speakers" path (Conversational Actions) was killed by Google in June 2023, and the proposed replacement (third-party Gemini Extensions) isn't broadly open as of early 2026. Smart Home actions are the wrong shape (IoT-only). With no Home Assistant install in the picture, the realistic home-use surface is **the phone itself** — Pixel + Pixel Buds / Pixel Watch puts Google Assistant in John's ear while he's cooking, watching TV, or otherwise away from his desk but inside the house.

**Goal.** Wire up Android App Actions on the phone so John can interact with Switchboard by voice via Google Assistant, without unlocking the phone or opening the app. Target invocations:

- *"Hey Google, ask Switchboard what's pending"* → reads the latest unanswered `ask_human` aloud (or a summary if it's long).
- *"Hey Google, tell Switchboard `<reply>`"* → writes a response to the active pending question via `responses/{request_id}`.
- *"Hey Google, ask Switchboard to start a session for `<favorite>`"* → spawn (stretch; same caveats as the Auto entry below).

**Mechanism.** Android App Actions via `shortcuts.xml` with custom intents (or Built-in Intents where one fits — `actions.intent.GET_THING`, `actions.intent.SEND_MESSAGE` are candidates worth checking against the App Actions catalog at spec time). The voice trigger lives on the phone; the response surface reuses whatever notification / TTS plumbing the [Android Auto](#android-auto-messaging-style-integration-for-ask_human-with-optional-voice-spawn) entry settles on.

**Relationship to other backlog items.**

- **Heavy overlap with the Auto entry above.** Both want: voice trigger → read question aloud → voice reply → write to Firebase. The Auto entry is the *surface* (Auto messaging bridge, DD compliance); this entry is the *voice trigger*. If the Auto entry lands first, App Actions on the phone is mostly registration + intent fulfillment glue.
- **Subset of [Multi-Surface Voice & Summary Integration](Multi-Surface%20Voice%20and%20Summary%20Integration.md) SB-05 (TTS) and SB-06 (`RemoteInput` STT)**, scoped to the phone-via-Assistant surface.
- **Sequencing suggestion (not a commitment):** spec the Auto entry and this entry together; they share enough that a single design pass covering "voice-driven `ask_human` interaction across Auto + Assistant" is cheaper than two.

**Explicitly out of scope.** Nest speakers, Nest Hub, and anything that requires the agent to live somewhere other than a phone. That's gated on third-party Gemini Extensions opening up; track separately if/when that becomes a real SDK.

**Trigger to pick up.** John finding himself wanting to interrupt cooking/TV to walk to the desk and check on an agent — i.e. real friction from the phone-not-in-hand-but-Assistant-in-ear case.

---

## Combined (server + client)

### Server presence heartbeat (offline indicator on the phone)

**Surfaced 2026-05-02.** Companion to the no-login spawn gate (`server/spawn.py:_user_has_interactive_session`) shipped the same day, which covers the "desktop on but no user logged in" failure case server-side. This entry covers the harder case the server can't detect itself: **desktop powered off / network unreachable**. Without something like this, an off-desktop spawn just times out silently — Firebase queues the command, the app shows nothing, John doesn't know whether to wait or go reboot the box.

**Target behavior.** A live "desktop online" indicator in the Android UI driven by a server heartbeat. When the desktop is unreachable (off, asleep, networked-off, service crashed), the indicator goes red and the spawn button surfaces "Desktop offline — your spawn will queue until it comes back" rather than producing no feedback at all.

**What it takes:**

- **Server.** A new dispatch task next to the existing loops in `server/gateway/dispatch.py`, e.g. `dispatch_presence_heartbeat`, writes `presence/server_alive_at: <iso-timestamp>` to Firebase RTDB on a 30s cadence. Sized like the other dispatch loops — `_BG_TASKS` registration, `LoopSupervisor` wiring for crash counting and `/healthz` reporting. Backend method `write_presence_heartbeat()` on `FirebaseBackend`.
- **Android.** `MainViewModel` subscribes to the `presence/server_alive_at` node, computes `staleSeconds = now - server_alive_at`, exposes a `serverOnline: Boolean` state (true when stale ≤ ~60s, false otherwise). One indicator surface — Page A app-bar icon and the spawn dialog — both bind to the same flag. When offline, spawn-button copy adapts to set expectations.
- **Tests.** Server side: dispatch loop writes on cadence, supervisor reports in `/healthz`. Android side: viewmodel state flips on stale data; UI binds correctly.

**Open questions for the design pass.**

- Cadence vs. staleness threshold. 30s write / 60s stale gives a 30-second worst-case false-online window after the server goes down; tightening burns a bit more Firebase write traffic for marginal UX gain. Loose default is probably right.
- Does the spawn button stay tappable when the indicator is red, queueing the command optimistically (Firebase already retains it), or does it block? "Tappable with explicit queue-until-online copy" feels right — matches the design where Firebase is the durable transport.
- Surface the offline state in the channel header too, or is the Page A indicator enough? Likely just Page A — the channel-level surface adds noise.
- One presence path or per-process? Single server, one heartbeat — keep it simple.

**Why not the simpler "client-side timeout on spawn ack" alternative.** A timeout-based detection (Option A in the original conversation: app waits N seconds for the spawn ack message and surfaces "no response" if absent) is cheaper to build but only fires *after* a failed attempt — John taps spawn, waits 10 seconds, gets a generic timeout error, has no way to know the desktop was the problem before he tried. The heartbeat lets him see status before he taps.

**Trigger to pick up.** Real friction from off-desk spawn attempts where the desktop is off — i.e. John finding himself wondering "did my spawn go through, or is the box just off?" If that's a once-a-month occurrence, the timeout-based fallback is fine; if it's weekly, build the heartbeat.

---

### Spawn dialog: "resume last session" option

**Surfaced 2026-05-02.**

**Use case.** Today every spawn from the Android UI starts a fresh Claude / Gemini session — no chat history, no compacted memory of prior work in the same `cwd`. When John wants to pick up where he left off ("finish what we were debugging this morning"), he has to re-page-in context from the journal / git log instead of letting the agent's own session memory do it.

**Target behavior.** Spawn dialog gains a "**Resume last session**" toggle. When set, instead of starting a clean spawn, the gateway resumes the most recent agent session in that `cwd` and injects the spawn prompt as the next user message.

**What it takes:**

- **Server.** `spawn.py` extended to accept a `resume_last: bool` flag. For Claude Code, append `--resume <session_id>` (or `--continue` if it's sufficient — needs a check on whether it's `cwd`-anchored). For Gemini CLI, the equivalent flag. Track per-cwd "last session id" — likely discoverable from each CLI's own session store; otherwise the gateway records it on each spawn.
- **Android.** Toggle in the spawn dialog (`SpawnSessionDialog.kt`). Greyed out with a tooltip ("no prior session in this cwd") when no resumable session exists. Surface the prior session's title in the dialog so John can confirm which one he's picking up.

**Open questions for the design pass.**

- Does "last session" mean last-of-this-agent or last-overall in the cwd? Probably last-of-this-agent, matching how each CLI tracks its own history.
- Both-Claude-and-Gemini spawns where only one has resumable history — degrade to fresh for the missing side, or block?
- Interaction with `_SPAWN_COLLISION_TIMEOUT_SECONDS` — resuming an active (not just recent) session is a collision, not a fresh start. The collision dialog already handles this; resume should funnel through the same path.

---

### Inbound document/log upload (phone → agent)

**Surfaced 2026-05-02.**

**Use case.** Today files only flow agent → John (`send_document_human`); the reverse has no path. Two motivating cases:

- **Screenshot share.** John spots an Android UI bug or an unexpected notification and wants to drop a screenshot into the active channel without round-tripping through email or scp.
- **Client log capture.** When debugging the Switchboard Android app itself, the agent often wants the on-device logcat / app logs. Asking John to grab them manually is high-friction and error-prone.

**Target behavior.**

- **Push from phone (user-initiated).** Attachment affordance on each channel in the Android UI (camera roll, file picker, take-photo). File uploads to Firebase Storage; metadata lands under `channels/{key}/inbound_documents/{doc_id}`. The agent picks it up via a new MCP tool (e.g. `receive_document_human(cwd) → path`) or as part of the next `ask_human` reply payload.
- **Pull by agent (log-specific).** New MCP tool `request_client_logs(cwd, sender)` causes the Android app to harvest its own logs (logcat filtered to the Switchboard package, plus any app-side structured logs) and upload them as an inbound document. The tool returns once delivery completes.

**What it takes:**

- Server: Firebase Storage path + registry entries for inbound documents, MCP tool(s) to receive them, separate tool / FCM data-message to trigger the client-side log harvest.
- Android: attachment picker UI per channel; FCM handler for the "harvest logs" command; logcat capture via the standard system API filtered to the app's package; upload-to-Storage path.
- Size and security caps mirroring `send_document_human` (5 MB, denied filename patterns).
- SKILL.md — when to use each direction, format expectations.

**Open questions for the design pass.**

- Delivery shape for user-pushed files: pull-on-demand (`receive_document_human`) vs auto-inject into the next `ask_human` reply payload? On-demand is cleaner; auto-inject is fewer round trips for the common "screenshot + question" case.
- Log harvest scope: app-package-only or full logcat? Privacy and size implications.
- Persistence: ephemeral per-session, or retained like outbound documents?
- Multi-file batch — one upload at a time, or a sequence?

---

### Pause button on each channel (collab interrupt)

**Surfaced 2026-05-02.**

**Use case.** Today there is no way to break into a running collab session mid-debate without ending it (`end_collab`) or waiting out the 24h `message_and_await_agent` timeout. A pause is the missing middle ground — interrupt both agents, force them to surface what they were doing, and have them ask John for further instructions before resuming.

**Target behavior.** A pause button on each channel tab in the Android UI. When pressed, all in-flight `message_and_await_agent` calls for that `cwd` immediately return with a sentinel message indicating John has paused the collab session and that the agents must ask him for further instructions before resuming. The session itself is preserved — enrollment state, pending injects, etc. — so the conversation can resume in place once John gives direction.

**What it takes:**

- Firebase + gateway state (`paused: bool` per `cwd`).
- New sentinel (e.g. `"__COLLAB_PAUSED__\n..."`) returned by `message_and_await_agent` when the flag flips during a pending call.
- Android channel tab: pause button (visible / enabled when any agent in that cwd is currently waiting), "PAUSED" indicator while active.
- SKILL.md update — document the sentinel and the required `ask_human` follow-up; pick a designation rule for which agent surfaces (parallel to the reporter rule in `end_collab`).

**Open questions for the design pass.**

- One agent reports / asks, or both? `end_collab` uses an explicit `hand_off_to_human` flag; pause needs the equivalent or a sensible default.
- Resume mechanic: does John's reply implicitly clear the flag, or is there an explicit unpause control?
- Does pause apply to single-agent `ask_human` channels too, or collab-only? (`ask_human` is already self-paced by John's reply, so pause arguably adds nothing there.)
- Interaction with the H8/H9/H10 turn-end hook invariants — the sentinel must leave both agents in a state where they can call `ask_human` without tripping "live partner is blocked" guards.

---

### Away-Mode Framing Check (terminal-leak detection mid-turn)

The Stop hook blocks any turn that ends without an `ask_human` / `notify_human` / `send_document_human` call while away-mode is active. SKILL.md "tool call IS the acknowledgment" reinforces that on the agent side. Together those cover the turn-end case.

What's still uncovered: terminal text emitted *before* the agent's first tool call within a turn ("Got it, on it…" → tool call). The hook doesn't see that — it only fires at turn-end. A stricter check would scan the agent's transcript for leaked text before the first acknowledging tool call and block / log on detection.

Low priority — practical leakage is rare given current SKILL adherence. Pickup when a real incident surfaces.

---

### Skill Instruction Polish

Periodically review and harden `SKILL.md` based on failure patterns (e.g., the 2026-04-23 terminal leak incident). Edit only the in-repo `skill/SKILL.md` — the user-level installs at `~/.claude/skills/switchboard/` and `~/.gemini/skills/switchboard/` are symlinks to it, so changes flow through automatically.

---

### Multi-Surface Voice & Summary Integration

**Proposed 2026-04-25.** Major multi-surface UX initiative spanning phone, Wear OS, and Android Auto. Introduces a Firebase Cloud Function (Gen 2) that uses Gemini 3.0 Flash to transform raw agent updates into a `display_metadata` object — surface-specific strings tuned for each device (`summary_phone`, `glance_wrist`, `speech_payload`, `progress_state`). Surfaces consume the metadata via `Notification.ProgressStyle` + `MessagingStyle`, with TTS read-aloud, RemoteInput voice-reply, an Android Auto messaging bridge, and a remote-session kill switch. Includes smart-throttling, offline cache for the last N speech payloads, and cross-surface notification cancel-sync.

Requires Firebase Blaze plan upgrade (Cloud Functions Gen 2). Spec breaks into 10 work items SB-01 through SB-10.

**Spec:** [`docs/Multi-Surface Voice and Summary Integration.md`](Multi-Surface%20Voice%20and%20Summary%20Integration.md).

---

### Timeout snooze via Android app

Add a "Snooze" button to the `ask_human` notification/tab that extends the window by 2h. Implementation: Android app writes `snooze: true` to the question object; gateway intercepts the change and resets the wait clock in the registry.

---

## Explicitly deferred / not recommended

- **Disconnect detection for unattended agent crash.** Investigated. Starlette/uvicorn does detect the dropped TCP/SSE connection, but FastMCP's transport doesn't propagate that to `ServerSession._in_flight[request_id]` or `responder.cancel()` — wiring it requires an upstream mcp-library patch, monkey-patching `ServerSession.__init__` from ASGI middleware, or a custom transport subclass. None are clean. Heartbeat alternative not pursued (per-turn round-trip not justified given the typical kill-and-respawn and cancel-tool-call paths are already covered). The 24h `ask_human` timeout is the backstop. Revisit if the MCP SDK adds a disconnect-propagation hook.
- **Gemini CLI cancel notifications.** Not actionable server-side. Per snoop-log evidence, Gemini CLI does not send `notifications/cancelled` over MCP when the user cancels a tool call. File an issue with the Gemini CLI repo if it matters; nothing to fix here.
- **Webhook instead of long-polling getUpdates.** Legacy Telegram concept, no longer applicable.
- **Multi-user chat support.** Single-developer model is baked into the spec. Don't touch until there's a concrete second user.
- **MarkdownV2** — Telegram flavour. Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message. Obsolete after Telegram removal.
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
