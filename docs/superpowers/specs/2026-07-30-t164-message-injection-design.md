# T-164: Free-form human-to-agent message injection + non-blocking ask_human — Design

**Date:** 2026-07-30
**Status:** Approved (design sections approved by John 2026-07-30; sections 1–2 via terminal AskUserQuestion + phone, sections 3–4 via phone)
**Backlog:** T-164 (subsumes the 2026-07-30 background-ask amendment). Relationship: T-004 (agent-side non-blocking sends) stays open and out of scope; the orchestrator runbook's haiku "parking attendant" interim workaround is retired by this feature once shipped.

## Problem

The phone and Operator can only *answer* a pending `ask_human` — there is no way to send unsolicited text into a live conversation. A heads-down agent (mid-tool-calls) is unreachable from the phone, assessed as the app's biggest usability gap. Separately, agents have no way to ask John a question without blocking their turn; the two problems share their hard half — delivering an inbound human payload to an agent that is not sitting in a wait.

## Verified platform constraints (CC hooks reference, checked 2026-07-30)

- `PostToolUse` hooks may emit `hookSpecificOutput.additionalContext`, documented to reach the model mid-turn. This is the only mid-turn injection channel; `reason` is not documented to reach the model on PostToolUse.
- There is **no mechanism to wake a fully idle Claude Code session** (turn ended, at the prompt) short of `claude --resume` or terminal input.
- MCP tool calls have **no partial/streaming results** — resolving the pending future is the only delivery channel to an agent blocked in `ask_human`.

These three facts shape the whole design: delivery is a ladder over the states an agent can actually be reached in.

## Decisions (John, 2026-07-30)

| # | Decision |
| --- | --- |
| D1 | A free-form message **resolves** a member's live blocking `ask_human` (agent unblocks immediately with the text as its reply; the question card cancels; the agent re-asks if unanswered). No queue-behind-the-ask, no per-message choice. |
| D2 | One background slot per session (in addition to its one blocking ask). A new background ask **appends** to the existing background card instead of superseding it — one card, one reply resolves the whole slot. |
| D3 | Append semantics apply **within the background slot only**. Blocking asks keep REV-106 supersede semantics; the blocking and background slots never supersede each other. |
| D4 | Free-form messages address **all members** (conversation-wide). Per-member targeting deferred. |
| D5 | Mid-turn delivery via **PostToolUse** notice injection (not turn-boundaries-only). |
| D6 | Background asks at desk take the **at-desk redirect** (sentinel; no phone card), consistent with blocking asks. |

## Section 1 — Server core: `message_commands` + the delivery ladder

### Command path

Phone/Operator push `{conversation_id, text, issued_at}` to a new top-level RTDB queue `message_commands/<push_id>` — the exact pattern of `combine_commands` / `force_end_commands`. A new supervised dispatch loop `dispatch_message_commands` (in `server/gateway/dispatch.py`: delete-on-dispatch, `_BG_TASKS` registration, `LoopSupervisor` wiring, `/healthz` reporting) consumes it. Entries are gated by the existing `command_freshness` TTL so a "stop everything" queued before a restart never fires stale.

### On dispatch (under the conversation lock)

1. Validate the conversation exists and is Active; otherwise send feedback to the phone (`send_text`) and drop the command.
2. Append John's message to `conv.messages` (`{seq, sender: "John", type: "human", text, timestamp}`) and write it to `/messages/<conv_id>` via `write_conversation_message` with `type="human"`, sender `"John"`, `format="markdown"`, FCM push suppressed (John authored it). Bump conversation last-activity. No `attached_to_msg_id` (it is not a reply).
3. Deliver to **every live member**, exactly one rung each:
   - **Rung 1 — live blocking pending:** resolve via `terminate_pending(resolve_text=...)` with framing `[John interjected - not a direct answer to your question] <text>`. Any notices attached to that pending are folded into the composed text (terminate_pending's resolve path does not consume `record.notices` today; the ladder composes them in before calling). The phone card cancels; the agent unblocks and may re-ask.
   - **Rung 2 — parked in `conv.wait_queue` (`message_and_await_agent`):** wake it. The message is already in history, so the existing `_compose_wake_payload` carries it. Unlike agent messages, a human message wakes **all** waiting live members, not just the FIFO-oldest (D4) — a wake-all variant alongside `_wake_one_from`. (`wait_queue` is the only wait primitive in current code; the `open_peer_future` lobby T-143 describes no longer exists.)
   - **Rung 3 — working or idle:** `session_registry.queue_notice(cli_session_id, ...)` with framing `John (from phone): <text>` — delivered mid-turn (PostToolUse, Section 3), at turn end (Stop hook block, already wired), or with the next prompt (UserPromptSubmit, already wired).
   - Dormant/lost members get nothing live; the message is in history and arrives with their resume / `join_conversation` replay.

### The ladder as a shared unit

The per-member delivery ladder is one function (new module `server/inbound.py`, or a `conversation_ops` helper — implementer's choice) with signature shape `deliver_inbound(registry, backend, session_registry, logger, conversation, payload_for(member) -> str)`. It is called by both the free-form dispatch loop (this section) and background-answer delivery (Section 2). Rung selection: live blocking pending → wait-queue entry → queue_notice.

### Known limitation (documented, accepted)

An idle at-desk agent sees a queued notice only at its next prompt — no wake mechanism exists. In away mode the state does not arise: the Stop hook forces every turn to end on a blocking ask.

## Section 2 — Non-blocking `ask_human` (the background slot)

### Tool shape

New optional parameter: `ask_human(question, sender, title?, format?, suggestions?, background=false)`. With `background=true` the handler runs the same validation, conversation resolution, sender canonicalization, and rate-limit consumption as today, then:

- writes the question message (`type="question"`, `request_id`, suggestions supported) and the `pending_questions` record with a new flag `background: true`;
- registers a **future-less** pending (a live-created parked pending — the chunk-7 machinery already understands these) in a separate background slot keyed `(conversation_id, cli_session_id)`;
- returns immediately with the envelope `{"status": "pending", "request_id": "..."}`.

The phone renders it exactly like any pending question. The at-desk gate applies before any of this (D6): at desk, `background=true` returns the sentinel `ERROR: John is at his desk. State your question in the terminal and continue working.` and creates no card.

### Registry changes

A second map, e.g. `Registry._background_pending: dict[tuple[str, str], PendingRequest]`, keyed like `_pending`. Consequences:

- `find_by_request_id` (the dispatch resolve path) searches both maps.
- Blocking-ask supersede (`Registry.add`) touches only `_pending`; background registration touches only `_background_pending` (D3). The away-mode turn-ending blocking ask therefore no longer kills an outstanding background question — the structural problem that forced the slot split.
- **Append (D2):** registering into an occupied background slot appends the question text to the existing record's `question` (separator: `"\n\nAlso: "`), writes an additional `type="question"` message under the **same** `request_id`, and updates the `pending_questions` record's `question_text`. One reply resolves the whole slot. The envelope returns the slot's (original) `request_id`.
- Pending counts / mirrors / `/healthz` include background pendings (they are pendings; the phone badge counts them via `pending_questions` as today).

### Answer delivery

John's reply arrives via the normal `answers/<conv_id>/<request_id>` path. `dispatch_responses` finds the background record (via the widened `find_by_request_id`), writes the reply into history spliced under the question (`attached_to_msg_id`), and delivers through the Section-1 ladder with payload `John answered your earlier question '<question>': <answer>`:

- Rung 1 resolves the member's live blocking ask with that payload. Accepted churn: answering the background card cancels the agent's current blocking card (it re-asks) — D1 applied uniformly.
- Rung 2 wakes **that member's** wait entry (targeted wake, not wake-all — the answer is session-directed; the reply is in history so the wake payload carries it).
- Rung 3 queues a notice — exactly today's `finish_parked_resolve` behavior, generalized. The Firebase `pending_questions` record cleanup from `finish_parked_resolve` is preserved on all rungs.

### Lifecycle (no MCP cancel exists — the call already returned)

Terminal paths: answered; session end (`handle_session_end` → `terminate_pending`); conversation force-end / combine cleanup; the away-exit **bulk drain** (`exit_global` resolves it with John's decision/default text — the agent learns John is back, delivered as a notice); the 72h parked-TTL sweep. No explicit cancel tool in v1 — a moot background question lingers until one of those fires (accepted; revisit if it bites).

### Hydration

`pending_questions` records carrying `background: true` rebuild into the background slot, not `_pending` — avoiding the key collision between a hydrated background ask and a hydrated (parked) blocking ask from the same session. Records without the flag hydrate exactly as today.

### Away-mode Stop-hook predicate: unchanged

A turn still may not end on only a background ask — an idle session cannot be woken, so the hook keeps forcing a blocking ask. The backlog's "new legal idle state" is resolved by **interruption** instead: the background answer punches through the blocking ask via rung 1.

## Section 3 — Hook changes

- **`agent-status-hook.py`:** on PostToolUse, read the `notices` array the `/agent_status` POST response already returns and, when non-empty, emit `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": <joined notices>}}` on stdout. Zero new HTTP calls (the POST already fires on every PostToolUse; T-194's latency concern is not worsened).
- **Server `/agent_status` route:** pop notices for `PostToolUse` events too (today UserPromptSubmit only). Pop-on-read keeps delivery at-most-once: whichever of PostToolUse / Stop / UserPromptSubmit fires first for a session delivers.
- **Stop hook (`turn-end-hook-away-mode.py`), away-mode AskUserQuestion guard, injector hook: unchanged.**
- **Plugin version bump required** (`.claude-plugin/plugin.json`) — hook script edits are version-gated by the plugin cache.
- **Antigravity:** turn-end delivery only in v1 (its Stop-analog already carries notices via the shared `/away-mode` + reason channel); agy mid-turn injection is out of scope.
- **SKILL.md:** document `background=true` (shape, envelope, when to use: questions that should not stall work; turns still end on a blocking ask), and the fact that John can now inject messages — including the framing prefixes agents will see.

## Section 4 — Client surfaces

### Android

- The conversation-view bottom bar becomes **always-visible for Active conversations**. With a pending selected, it is today's answer path, unchanged. With none selected, placeholder "Message the agents…" and send pushes `{conversation_id, text, issued_at}` to `message_commands` via a new `MainViewModel.sendMessageToConversation(convId, text)` (through `writeReporting`, like every other command writer).
- No optimistic local echo in v1 — the server's history write lands sub-second and renders as a normal `type:"human"` bubble (verified: rendering is type-agnostic; `type:"human"` already gets John's byline treatment).
- Wear: reply-only, unchanged in v1.

### Operator

- New pure builder `messageCmd(convId, text, nowIsoFn)` in `dashboard/commands.js` returning `{path: 'message_commands', value: {conversation_id, text, issued_at}}`; store method wired through `guardedWrite('detail', ...)` with `fb.pushValue`.
- Always-visible composer in `ConversationDetail` under the pending stack, Active conversations only. Messages render out of the box as standard `.msg` bubbles.

## Invariants and failure modes

- **H8/H9/H10 turn-end invariants untouched:** no new agent-side non-blocking sends (T-004 stays open, separate); human messages and background asks never legalize a silent agent idle.
- Message write + full delivery ladder run **under the conversation lock**; the dispatch loop processes commands serially.
- Commands are freshness-gated and deleted on dispatch; unknown/ended conversation → phone feedback via `send_text`, command dropped.
- **No rate limit** on `message_commands` (human-paced, like the answers path). FCM suppressed on John's own messages.
- Idle-at-desk delivery gap documented above (Section 1).

## Out of scope (v1)

Per-member targeting (D4), Wear composer, agy mid-turn injection, explicit background-cancel tool, agent-side non-blocking sends (T-004). Runbook follow-up: retire the haiku parking-attendant pattern in `docs/superpowers/background-orchestrator-runbook.md` in favor of a real background ask after this ships.

## Testing

- **pytest:** dispatch loop (freshness gate, delete-on-dispatch, unknown/ended conversation feedback); each ladder rung in isolation and per-member rung selection (resolve blocking pending incl. notice fold-in, wake-all waiters for messages, targeted wake for background answers, queue_notice fallback); background create / append (same request_id, question growth) / answer / at-desk redirect / envelope shape; hydration routing (`background: true` → background slot; unflagged → `_pending`); away-exit bulk drain includes background records; TTL sweep; `/agent_status` PostToolUse notice pop (and UserPromptSubmit unchanged); `/healthz` registration of the new loop.
- **node --test (dashboard):** `messageCmd` builder shape; store send + Active-only gate.
- **Android:** build green; unit coverage for the new ViewModel writer where feasible.
- **Live smoke:** extend `scripts/smoke/smoke.py` with a message-inject round trip (command → history write → notice/resolve observed) and a background-ask round trip (ask → answer → notice delivery), respecting the existing restart-safety flags.
