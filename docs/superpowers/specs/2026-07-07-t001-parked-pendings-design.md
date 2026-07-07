# T-001 Parked Pendings - Design

**Date:** 2026-07-07
**Status:** Approved by John (brainstormed 2026-07-07, during chunk 4 execution)
**Parent:** `2026-07-06-t001-retriage-decision.md` (scope verdict: park instead of cancel, hold late answers, deliver on reattach). Sequenced after convening chunk 5 per that doc; this spec is the "own spec" the re-triage promised the implementing chunk. Grounded against the post-chunk-3 tree (`9b83580`); the plan re-grounds after chunk 5 lands.

## What this fixes

Server restarts currently stack three losses (re-triage doc): in-flight `ask_human` futures die with the process; the startup sweep (`sweep_orphaned_pending_questions`) actively cancels every `pending_questions` record, so John's unanswered questions visibly evaporate from the phone; and stateful MCP HTTP severs the agent transport regardless. This chunk fixes the first two. The third is out of scope by physics: the original in-flight tool call can never be revived server-side, and the startup away-mode auto-clear (which lets orphaned agents fall back to terminal output) STAYS - parking saves the questions, not the tool call.

## Design

### Parked representation (no second datastore)

- `PendingRequest.future` becomes `asyncio.Future | None`. **Parked = record without a future**, held in the same `Registry._pending` map keyed by `(conversation_id, cli_session_id)` - counts, supersede, and request-id lookup work unchanged, with `future is None` guards at the touch points (`add`'s supersede cancel, `resolve`'s set_result, bulk-respond drains).
- The ask path persists two additional fields into the RTDB `pending_questions` record at creation: **`cli_session_id`** and **the ask timestamp**. Today's record (request_id, sender, msg_id, question text, suggestions) lacks the session key; persisting it makes hydration direct instead of reconstructing via the session registry's `awaiting_human` state.
- Startup: `sweep_orphaned_pending_questions` is DELETED. `server/hydration.py` instead rebuilds each surviving `pending_questions` record as a parked `PendingRequest` (`started_at` from the persisted timestamp; records missing the new fields - written by a pre-chunk server - are cancelled as today, one final time, rather than guessed at).

### Answer arrival for a parked pending

In `dispatch_responses`, the `resolve() is None` branch consults parked records BEFORE the replay/unknown fallbacks (the landed order: `was_recently_resolved` -> replay-ignore, else "unknown_correlation" + stale-reply notice). A parked match:

1. Runs the normal history write (`write_conversation_message(cid, "John", "human", text, attached_to_msg_id=record.msg_id)`) - the answer lands in the conversation transcript and the phone bubble goes RESPONDED, exactly as a live resolve does.
2. Records `_record_resolved(conversation_id, request_id)` so answer-slot replays stay quiet.
3. Removes the parked record (+ pending-mirror decrement) and deletes the response slot.
4. Queues a chunk 3 session notice for the asking `cli_session_id`: `John answered your earlier question '<question>': <answer>` - delivered at the session's next turn boundary (Stop-hook block) or John's next prompt (UserPromptSubmit context), the same rails convene wakes use. If the session never returns, the notice dies with the session record at retention; the answer itself is already in the conversation history either way.

Decided 2026-07-07: history write + session notice; the "re-ask short-circuit" variant (next `ask_human` returns the held answer directly) was rejected - wrong answer delivered when the new question differs, and the notice already covers the retry case.

Two consumers of the pending record adapt to the parked case:

- **Attached notices:** chunk 3's convene wake appends to `pending.notices` for prepend-at-resolve; a parked resolve has no future to prepend into, so the parked path folds `record.notices` into the queued session notice (convene notice first, answer notice after) - this is the implementation detail that keeps the re-triage doc's "wake matrix works identically with parked pendings" claim true.
- **Bulk respond:** `exit_global`'s drain (`_apply_bulk_respond_decision`) treats parked records exactly like arriving answers - the parked-resolve path (history write, `_record_resolved`, notice, record removal) runs with the drain's decision text; nothing calls `set_result` on a missing future.

### Re-ask recovery (free via supersede)

A relaunched agent's natural move is asking again. `Registry.add`'s existing supersede path replaces the parked record (guarding the `None` future - nothing to cancel) and returns `prior_request_id` so the caller cancels the prior Firebase record, exactly as for a live supersede. Phone result: the old bubble cancels, the new one appears. No dedupe machinery.

### Lifetimes

- **Answered parked pendings** need no rule - the answer resolves them whenever it arrives.
- **Unanswered parked pendings expire at the session-retention horizon** (72h; reuse `SWITCHBOARD_SESSION_RETENTION_HOURS` - one "recent work" horizon across the product, decided 2026-07-07). A sweep pass (riding the existing `dispatch_session_sweep` cadence) cancels the Firebase record - the bubble greys out exactly as today's startup sweep does, just 72h later and only for truly-abandoned questions - drops the parked record, and logs to JSONL.

### Roster honesty (rides along)

`SWEEP_EXEMPT_STATES` exempts `awaiting_human`/`awaiting_agent` sessions from lost-marking because "the pending future is the liveness proof" - false for parked records and for hydrated `awaiting_*` sessions generally (wait-queue entries are in-memory too). The exemption becomes conditional on an actual live blocking structure (a future-bearing pending for `awaiting_human`; a wait-queue entry for `awaiting_agent`); a dead session stuck on `awaiting_human` after a restart becomes `lost`-markable on the normal silence threshold.

### Surfaces

- **Phone:** zero app changes. Bubbles survive restarts; answering one looks identical.
- **`/healthz`:** the `pending` block gains a `parked` count beside `count`.
- **Operator/Watchtower:** no changes (pending aggregation already derives from `pending_questions`).

## Non-goals

- Reviving the original in-flight `ask_human` call (loss 3; impossible under stateful MCP HTTP).
- Removing the startup away-mode auto-clear (orthogonal mitigation; stays).
- A second datastore - parked records hydrate from the existing `pending_questions` tree.
- Cross-session answer delivery (a DIFFERENT session picking up another session's held answer); the conversation history write already covers observers.

## Testing sketch

Hydration round-trip (record -> parked pending -> counts); parked resolve does history write + notice + `_record_resolved` + slot delete; legacy records without the new fields are cancelled once; re-ask supersedes a parked record and cancels the old Firebase bubble; TTL sweep cancels only unanswered parked pendings past the horizon; conditional sweep exemption (parked `awaiting_human` session goes `lost`; live-future session stays exempt); answer replay after a parked resolve is ignored; bulk-respond (`exit_global` drain) handles `future is None` records.
