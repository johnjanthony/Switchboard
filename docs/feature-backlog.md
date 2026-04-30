# Switchboard Feature Backlog

Open/proposed features for Switchboard, grouped by where the work lives. Shipped items have been moved to [`../PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md). When an item here is picked up, it gets its own spec + plan per the existing workflow.

---

# Server

## Persistence layer (Firebase hybrid write-behind)

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

## `MessengerBackend` trait split (god-interface refactor)

**Surfaced 2026-04-28** in the codebase review (`docs/2026-04-28-codebase-review.md` H4). Originally HIGH, demoted to MEDIUM after H1 persistence was deferred — the "prep work for clean persistence wiring" justification disappeared with H1, leaving only the testability argument.

**Problem.** `server/messenger.py` declares ~20 abstract or no-op methods, the majority of which are pure Firebase semantics: `write_away_mode_mirror`, `start_away_mode_listeners`, `load_away_mode_snapshot`, `delete_legacy_away_mode_node`, `reset_all_pending_responses`, `start_inject_listener`, `poll_inject_messages`, `write_spawn_collision_prompt`, `clear_spawn_collision_prompt`, `wipe_channel`, `set_channel_hidden`, `fetch_message_text`, `read_channel_meta`, `has_messages`, `poll_away_mode_commands`, `poll_spawn_collision_decision`. The "pluggable backends" promise in `AGENTS.md` is unsupported by this surface — replacing Firebase would require reimplementing 80% of these methods. `server/main.py` also does `if isinstance(backend, FirebaseBackend)` — a tell that the abstraction has leaked.

**Impact.** Tests have to mock 20+ methods on a single class. New features tend to add more. The class as a single test seam is unwieldy.

**Target fix.** Split into focused traits:

```
MessageWriter       — write_channel_message, mark_question_cancelled, send_*_followup
AwayModeMirror      — write_away_mode_mirror, load/start listeners, reset_pending
SpawnCollisionPort  — write_spawn_collision_prompt, clear, poll_decision, wipe_channel, etc.
InjectPort          — start_inject_listener, poll_inject_messages
ResponsePoller      — poll_responses, poll_commands, poll_away_mode_commands
```

`FirebaseBackend` implements all five. `gateway.py` accepts only the ports it needs per handler. Tests can supply minimal fakes per port instead of mocking the full surface.

**Effort estimate.** ~1 day. Mechanical split, no behavior change. Pairs naturally with H1 (persistence) when that ships — the persistence layer would extend `MessageWriter` / `AwayModeMirror` rather than the monolithic `MessengerBackend`.

---

## Listener thread supervision (M1)

**Surfaced 2026-04-28** in the codebase review (`docs/2026-04-28-codebase-review.md` M1).

**Problem.** `server/firebase.py:start_away_mode_listeners` spawns `threading.Thread(target=…, daemon=True)` for the away-mode global and channel listeners. If a listener thread dies (network blip, exception inside `.listen()`), the in-memory cache freezes and the bug surfaces only when away-mode toggles silently fail to apply. There's no liveness signal.

**Target fix.** Wrap each listener in a supervised loop with reconnect + exponential backoff. Expose a per-listener "last event at" timestamp on `/healthz` so silent listener death is observable.

**Effort estimate.** ~half day. Pairs nicely with M2 (deeper `/healthz`) since both feed the same operational-visibility story.

---

## Deeper `/healthz` + crash-alert cadence (M2)

**Surfaced 2026-04-28** in the codebase review (`docs/2026-04-28-codebase-review.md` M2).

**Problem.** Two related operational-visibility gaps:

1. `server/main.py` `/healthz` reports only `pending_count`, `oldest_pending_age_seconds`, `total_answered`. Silent on dispatch-loop crash counts, FCM failure rate, listener thread liveness.
2. `server/gateway/dispatch.py:_loop_crash_backoff` pages once at `consecutive_failures == 5` and never again. A 30-minute outage produces one alert and then silence — operator goes blind after the first salvo.

**Target fix.**

- Add per-loop `crash_count` and `last_crash_at` fields to `/healthz` (covers all four dispatch loops: `dispatch_responses`, `dispatch_commands`, `dispatch_inject_queue`, `dispatch_away_mode_commands`).
- Change the crash alert to fire on a cadence — every Nth failure with N doubling (5, 10, 20, …) — rather than exactly once. Sustained outages produce ongoing visibility instead of silence after the first.
- Combine with M1's listener-liveness timestamps so the endpoint becomes a single operational dashboard surface.

**Effort estimate.** ~half day. Stack with M1 in a single sweep.

---

## Non-blocking partner messaging — *tentative; needs design pass* (collab protocol enhancement)

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

**Surfaced 2026-04-26** during cwd-as-channel post-merge testing (test 6d). Several scenarios; partial coverage shipped over time:

- **Kill-and-respawn (typical dev workflow)** — covered by **cancel-on-spawn** (shipped 2026-04-27): `Registry.cancel_pending_for_cwd` + `SpawnHandler._cancel_prior_pending` write the WITHDRAWN marker via `mark_question_cancelled` before the new agent launches.
- **User cancels the tool call from the terminal (Claude Code)** — covered as of 2026-04-30: stateful HTTP transport + `anyio.CancelScope(shield=True)` cleanup shielding in [`server/gateway/handlers.py`](../server/gateway/handlers.py) means MCP `notifications/cancelled` reaches our `CancelledError` block AND the cleanup write completes. Question gets `cancelled: true`; phone shows WITHDRAWN.
- **Gemini CLI cancel** — NOT covered. Per-snoop-log evidence on 2026-04-30, Gemini CLI does not send `notifications/cancelled` over MCP at all. Either a Gemini-side bug or a deliberate omission. Nothing fixable server-side; file an issue with the Gemini CLI repo if it matters.
- **Unattended long-running agent crash (process death without explicit cancel)** — STILL OPEN. Verified: closing the agent's terminal entirely leaves the question with `cancelled: false`, `pending_responses: 1`. A reply lands as if delivered to the (now-dead) agent. FastMCP's stateful streamable-HTTP transport doesn't propagate the dropped TCP/SSE connection to the in-flight responder; the session task keeps the responder alive. The pending entry sits until the 24h `ask_human` timeout fires `send_timeout_followup`. So the user-visible bug ("phone shows RESPONDED but agent never got it") still happens in this scenario.

Two complementary approaches remain as future insurance for the unattended-crash case:

1. **HTTP keepalive disconnect detection.** Investigate whether FastMCP's stateful-mode session lifecycle (now in use as of 2026-04-30) surfaces a disconnect signal that can be plumbed into CancelledError on in-flight responders. May be tractable now that we're stateful — sessions HAVE a clearer lifecycle than they did in stateless.
2. **Agent liveness pings.** Heartbeat protocol: agents send periodic keepalives; missing two in a row = mark all their pending questions cancelled. More moving parts, more state, but transport-agnostic.

Both are insurance against unattended crashes; common cases are now covered.

---

## Away-Mode Framing Check (terminal-leak detection mid-turn)

The Stop hook blocks any turn that ends without an `ask_human` / `notify_human` / `send_document_human` call while away-mode is active. SKILL.md "tool call IS the acknowledgment" reinforces that on the agent side. Together those cover the turn-end case.

What's still uncovered: terminal text emitted *before* the agent's first tool call within a turn ("Got it, on it…" → tool call). The hook doesn't see that — it only fires at turn-end. A stricter check would scan the agent's transcript for leaked text before the first acknowledging tool call and block / log on detection.

Low priority — practical leakage is rare given current SKILL adherence. Pickup when a real incident surfaces.

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
