# Switchboard Feature Backlog

Open/proposed features for Switchboard, grouped by where the work lives. Shipped items have been moved to [`../PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md). When an item here is picked up, it gets its own spec + plan per the existing workflow.

---

# Server

## Wire spawn-collision dialog into the `/spawn` command path

**Surfaced 2026-04-26** during cwd-as-channel post-merge testing (tests A & B). The collision-detection plumbing is half-built and never gets invoked: `SpawnHandler.submit()` and `resolve_collision()` exist with the right logic (`has_messages` → `write_spawn_collision_prompt`, then on user decision `wipe_channel`/`set_channel_hidden`/launch), but `_handle_spawn` (the path `/spawn` commands take) goes straight to `_handle_single_spawn` / `_handle_collab_spawn`, bypassing the check entirely. Additionally, nothing on the server listens for the phone's decision write to `spawn_collisions/{spawn_id}/decision` — there's no `poll_spawn_collision_decision` analog to `poll_bulk_respond_decision`. So even if the dialog *were* fired, the user's choice would never be acted on.

**Why this didn't surface before today:** pre-Fix-#2 (spawn channel routing), `channel_id` was always a unique synthetic `project-YYYYMMDD-HHMMSS` string, so `has_messages(canonical_cwd)` would never match an existing channel — the collision flow was unreachable. With deterministic cwd-keyed channel ids, re-spawning into the same cwd now genuinely collides, exposing the gap. Test A "passes" only by accident (no-collision-check fallthrough resembles the Continue path); test B (Clear) is fully blocked.

**Scope of fix** (multi-file, comparable to one of the original Slices E–K):

1. `spawn.py` — `_handle_spawn` calls `has_messages` / `write_spawn_collision_prompt` before delegating to `_handle_single_spawn` / `_handle_collab_spawn`; awaits the decision; routes to wipe-then-launch or just launch.
2. New `poll_spawn_collision_decision` mechanism — Firebase listener on `spawn_collisions/{spawn_id}/decision`, future resolved when written.
3. `messenger.py` — abstract method + MultiBackend fan-out.
4. `firebase.py` — `_on_spawn_collision_decision` listener; queue-and-future plumbing mirroring `poll_bulk_respond_decision`.
5. Server main loop — wire the spawn-handler's wait into the existing event flow.

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

## Symmetric spawned collab sessions

Remove `_LISTENER_NOTE` from Agent 2's spawn prompt in `spawn.py` so both spawned collab agents receive the same task prompt and work in parallel before exchanging findings — matching the BYO session model. Currently Agent 2 is explicitly told to call `message_and_await_agent` with no message, which prevents parallel work. The `_pending` queue in `CollabSession` already handles the both-with-messages case correctly, so this is purely a prompt change. The collab protocol hint in SKILL.md ("the first response you receive may be your partner's independent opening position") already documents the expected behaviour.

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

## Android: multi-sender reply UX in BYO collab channels

**Surfaced 2026-04-26** during cwd-as-channel post-merge testing (test F). When a single channel has multiple pending questions from distinct senders (BYO collab scenario), the reply bar in `SessionViewScreen.kt:87` picks one via `currentPending.values.firstOrNull { !it.cancelled }` and shows just that one in the bottom bar. The placeholder reads `"Reply to {sender}…"` but it's subtle, easy to miss, and the user has no agency over which pending to reply to first — the UI implicitly serializes them.

**What needs to change:**

- **Strong sender attribution in the reply UI.** Promote the sender from a placeholder hint to a prominent label or chip directly above/beside the reply input — e.g. a `→ Replying to: [Claude]` row with the sender styled like the message-bubble sender label.
- **Agency over which pending to answer.** Options:
  - Render an inline reply affordance directly beneath each pending question card in the scroll, not (only) a global bottom bar. Eliminates the ambiguity entirely — each question has its own visible reply box.
  - Or: if keeping the single bottom bar, add a row of sender chips (`[Claude] [Sparkles]`) above it that the user can tap to switch which pending the bar targets.
- **Test:** when two senders both have pending questions in the same channel, the user can pick which one to answer first, and the reply visibly routes to that sender (slot `responses/{cwdKey}__{sender}` for the chosen one).

The data is already correct end-to-end (verified in test F: server's `(cwd, sender)` keying preserves both pendings, replies route correctly per slot). Pure UX work on the phone.

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

# Combined (server + client)

## Per-channel away-mode tracking — IN IMPLEMENTATION (cwd-as-channel branch, post-Slice-M validation)

This entry graduated from backlog to active work and expanded in scope during brainstorming to **cwd-as-channel unification**: replacing `channel_id` with canonical-cwd as the namespace, re-keying blocking exchanges by `(cwd, sender)` with supersede semantics, two-tier away-mode (`_global_away` + `_cwd_overrides`), and an Android UI overhaul to a list-based two-page nav.

**Spec:** [`superpowers/specs/2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md`](superpowers/specs/2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md)
**Plan:** `superpowers/plans/2026-04-25-cwd-as-channel-and-per-cwd-away-mode.md` (gitignored)
**Branch:** `cwd-as-channel`
**Status (2026-04-26):** Slices A–L committed; today's spawn-correctness fixes shipped (cwd-override on spawn, deterministic cwd-keyed channel id, mirror sync on bulk-clear). Slice M validation walked end-to-end on phone and watch — most scenarios pass; gaps surfaced as separate backlog items (spawn-collision dialog wiring, withdraw-on-agent-death, multi-sender reply UX, swipe gestures, FCM notification suggestion actions). 321 server tests passing.

The original concerns this entry tracked are all subsumed by the new spec:
- Per-channel away state → covered by Slice B (two-tier `_global_away` + `_cwd_overrides`).
- At-desk `ask_human` redirect under per-channel state → covered by Slice D (per-cwd resolution in `gateway.ask_human`).
- Reply-routing by current-pending pointer → covered by Slice C (re-key `_pending` by `(cwd, sender)` so the slot itself is the pointer; supersede on add).

The bundled "hook captures leaked terminal text" enhancement is **not** included in the current spec — it remains a separate follow-up and could be bolted on later (the Stop hook now knows the cwd, so plumbing it through `notify_human` on the channel is straightforward).

This entry moves to `PROJECT-JOURNAL.md` once the branch merges to main.

---

## Withdraw pending questions when the agent process dies

**Surfaced 2026-04-26** during cwd-as-channel post-merge testing (test 6d). Killing an agent mid-`ask_human` leaves the question hanging on the server and the phone — no "WITHDRAWN" indicator appears.

**The mechanics already exist; the trigger doesn't fire:**
- Server has the cancellation path at `gateway.py:226,246` (`except asyncio.CancelledError: await _safe_mark_cancelled(...)` → writes `cancelled: true` via `mark_question_cancelled`).
- Phone renders it in `MessageBubble.kt:48-60` (WITHDRAWN badge + 0.5 alpha).

**Why the trigger doesn't fire on process kill:** MCP transport is streamable HTTP. HTTP doesn't reliably surface mid-call client disconnects to the server handler, so `asyncio.CancelledError` is never raised. The pending future just hangs until `SWITCHBOARD_TIMEOUT_SECONDS` (default `86400` = 24h), at which point the **timeout** branch fires — `send_timeout_followup`, not `mark_question_cancelled`. Two different gateway paths, two different UX outcomes.

**What does trigger cancellation today:** supersede by a newer `ask_human(cwd=X, sender=Y, ...)` (`gateway.py:219-223`), and server shutdown (`_safe_mark_cancelled` in cleanup).

**Possible approaches (smallest to largest scope):**

1. **Cancel-on-spawn.** When a spawn lands on a cwd, walk `registry.cwd_overrides()` / pending requests for that cwd and mark any in-flight questions cancelled before the new agent starts. Solves the common "I killed the agent and respawned" case directly. Minimal change, no new infrastructure.
2. **HTTP keepalive disconnect detection.** Investigate whether MCP's streamable-HTTP transport surfaces a disconnect signal to in-flight tool handlers (FastMCP / `streamable_http.py`). If yes, plumb that into a CancelledError on the awaiting future. May not be reachable depending on the MCP SDK.
3. **Agent liveness pings.** Heartbeat protocol: agents send periodic keepalives; missing two in a row = mark all their pending questions cancelled. More moving parts, more state, but transport-agnostic.

Approach 1 covers the testing scenario and most realistic dev workflows. Approaches 2/3 are insurance for unattended long-running agents that crash without a respawn.

---

## Away-mode Firebase schema reorganization

**Proposed 2026-04-26**, surfaced during cwd-as-channel post-merge testing. Two related schema changes that the current `away_mode/{global, overrides/{cwdKey}}` shape made awkward:

1. **Co-locate per-channel away-mode with the channel.** Move `away_mode/overrides/{cwdKey}` → `channels/{cwdKey}/away_mode`. The override conceptually belongs to the channel; co-locating gets lifecycle alignment for free (deleting/wiping a channel removes its override too, no orphan-on-channel-delete bug). Phone-side it removes one Firebase listener — the channel listener already covers it.

2. **Group global settings.** Move `away_mode/global` → `global_settings/away_mode`. `global_settings/` becomes the home for any future top-level switches (notification quiet hours, default sender, etc.); today it has one tenant. Once both moves land, the `away_mode/` node can be deleted entirely.

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

Periodically review and harden `SKILL.md` based on failure patterns (e.g., the 2026-04-23 terminal leak incident). Touches both the in-repo `skill/SKILL.md` and the user-level copy at `~/.claude/skills/switchboard/SKILL.md` that agents consume.

---

## Timeout snooze via Android app

Add a "Snooze" button to the `ask_human` notification/tab that extends the window by 2h. Implementation: Android app writes `snooze: true` to the question object; gateway intercepts the change and resets the wait clock in the registry.

---

# Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** Legacy Telegram concept, no longer applicable.
- **Multi-user chat support.** Single-developer model is baked into the spec. Don't touch until there's a concrete second user.
- **MarkdownV2** — Telegram flavour. Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message. Obsolete after Telegram removal.
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
