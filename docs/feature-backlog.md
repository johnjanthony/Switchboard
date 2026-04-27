# Switchboard Feature Backlog

Open/proposed features for Switchboard. Shipped items have been moved to [`../PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md). When an item here is picked up, it gets its own spec + plan per the existing workflow.

---

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

## Resilience and Protocol Enforcement

- **Silence Detection at the Gateway.** *(Spec'd 2026-04-23 as "Away-Mode Enforcement" — Stop-hook + server-side flag approach replaces gateway transcript inspection. See [`superpowers/specs/2026-04-23-away-mode-enforcement-design.md`](superpowers/specs/2026-04-23-away-mode-enforcement-design.md).)*
- **Away-Mode Framing Check.** Add an automated check to ensure that every agent response in away mode starts with a tool call.
- **Skill Instruction Polish.** Periodically review and harden `SKILL.md` based on failure patterns (e.g., the 2026-04-23 terminal leak incident).

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

## Per-channel away-mode tracking — IN IMPLEMENTATION (Slices A–L complete in working tree, Slice M pending)

This entry has graduated from backlog to active work. Implementation expanded in scope during brainstorming to **cwd-as-channel unification**: replacing `channel_id` with canonical-cwd as the namespace, re-keying blocking exchanges by `(cwd, sender)` with supersede semantics, two-tier away-mode (`_global_away` + `_cwd_overrides`), and an Android UI overhaul to a list-based two-page nav.

**Spec:** [`superpowers/specs/2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md`](superpowers/specs/2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md)
**Plan:** `superpowers/plans/2026-04-25-cwd-as-channel-and-per-cwd-away-mode.md` (gitignored)
**Branch:** `cwd-as-channel`
**Status (2026-04-25):** Slices A–L complete in working tree (no commits per CLAUDE.md). 321 server tests passing, Android `compileDebugKotlin` clean. Slice M (manual phone validation, 10 scenarios) is the only remaining work. See `docs/project_next_session.md` for full handoff details.

The original concerns this entry tracked are all subsumed by the new spec:
- Per-channel away state → covered by Slice B (two-tier `_global_away` + `_cwd_overrides`).
- At-desk `ask_human` redirect under per-channel state → covered by Slice D (per-cwd resolution in `gateway.ask_human`).
- Reply-routing by current-pending pointer → covered by Slice C (re-key `_pending` by `(cwd, sender)` so the slot itself is the pointer; supersede on add).

The bundled "hook captures leaked terminal text" enhancement is **not** included in the current spec — it remains a separate follow-up and could be bolted on later (the Stop hook now knows the cwd, so plumbing it through `notify_human` on the channel is straightforward).

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

## Android: suggestion buttons as notification actions

When `ask_human` is called with suggestions, render them as tappable action buttons on the notification banner so the developer can reply without opening the app.

**What it takes:**

- **Server (`firebase.py`)** — include suggestions as a JSON-encoded string in the FCM data payload alongside `request_id` and `channel_id`
- **New `NotificationReplyReceiver`** — a `BroadcastReceiver` that fires silently when an action button is tapped, writes the answer directly to Firebase `responses/{request_id}`, and dismisses the notification
- **FCM service** — parse suggestions from data payload, add up to 3 `addAction()` calls to the notification builder
- **Manifest** — register the receiver

**Constraint:** action buttons appear on the *expanded* notification, not the collapsed heads-up banner — the user swipes down on the banner to reveal them. Still faster than opening the app.

---

## Observability + reliability

- **Log rotation.** `logs/switchboard.jsonl` grows forever. At low volume this is a months-out concern, but worth a simple size-based rotation (`logs/switchboard.jsonl.1`, `.2`, with a cap).
- **`ask_human` rate limiting.** Per-channel token bucket on `notify_human` and `send_document_human` shipped 2026-04-23; `ask_human` is not yet rate-limited. Low priority — `ask_human` is self-paced by the human reply, unlike fire-and-forget notifications.
- **Timeout snooze via Android app.** Add a "Snooze" button to the `ask_human` notification/tab that extends the window by 2h. Implementation: Android app writes `snooze: true` to the question object; gateway intercepts the change and resets the wait clock in the registry.

---

## Maintenance & Housekeeping

- **Database ageout sweep.** Periodically clean up old questions, responses, and documents from Firebase (e.g., delete entries older than 30 days). This prevents the Realtime Database and Storage from growing indefinitely and keeps the Android app's history retrieval performant.

---

## Symmetric spawned collab sessions

Remove `_LISTENER_NOTE` from Agent 2's spawn prompt in `spawn.py` so both spawned collab agents receive the same task prompt and work in parallel before exchanging findings — matching the BYO session model. Currently Agent 2 is explicitly told to call `message_and_await_agent` with no message, which prevents parallel work. The `_pending` queue in `CollabSession` already handles the both-with-messages case correctly, so this is purely a prompt change. The collab protocol hint in SKILL.md ("the first response you receive may be your partner's independent opening position") already documents the expected behaviour.

---

## Explicitly deferred / not recommended

- **Webhook instead of long-polling getUpdates.** Legacy Telegram concept, no longer applicable.
- **Multi-user chat support.** Single-developer model is baked into the spec. Don't touch until there's a concrete second user.
- **MarkdownV2** — Telegram flavour. Its 18-character escape list (including `.` and `-`) makes unescaped user strings a footgun; one stray period rejects the whole message. Obsolete after Telegram removal.
- **Java rewrite** (considered 2026-04-20): no meaningful gain over NSSM for a single-developer tool. Python MCP SDK is the reference implementation; rewrite cost not justified.
