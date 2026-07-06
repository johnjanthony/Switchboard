# T-001 Re-Triage: Pending-Question Survival — Decision

**Date:** 2026-07-06
**Status:** Decided (John, 2026-07-06)
**Context:** Architecture review finding D5 (`2026-07-01-architecture-review-goal-drift.md`) asked for T-001's priority to be re-argued against the hub identity rather than the 2026-04 desk-side-tool identity. This note records the argument and the verdict. No design beyond the scope sketch below; the implementing chunk gets its own spec.

## What restart amnesia actually is (three stacked losses)

1. **In-flight `ask_human` futures die with the process** — the original T-001, inherent to in-memory futures.
2. **The startup sweep actively cancels every `pending_questions` record** (`sweep_orphaned_pending_questions`, wired in `main.py`): from the phone's point of view, John's unanswered questions visibly evaporate on every restart, marked cancelled.
3. **Stateful MCP HTTP severs the agent's transport session on restart** regardless of server-side memory: a revived pending still has no live tool call to resolve into. The agent needs `/exit` + relaunch (documented in `CLAUDE.md`). This loss is not fixable server-side and caps what "survival" can ever mean.

## What changed since the non-goal was declared

- **Restarts are no longer always chosen.** NSSM always-on service: crashes, host reboots, and Windows updates restart the server without anyone deciding to.
- **The hub identity raises the cost.** Ambient always-on supervision makes "the hub forgot every in-flight question" a product failure rather than a tool quirk.
- **Tractability improved with chunk 1 (D4 + session registry).** Answers resolve by `(conversation_id, request_id)`; `pending_questions` records already persist request_id, sender, msg_id, question text, and suggestions; the session registry records which session was `awaiting_human`. Reconstruction at hydration is a bounded piece of work, not an architecture change.

## The achievable scope: parked pendings

Full survival is impossible (loss 3). The achievable middle tier:

- Stop cancelling `pending_questions` at startup; rehydrate them as **parked** pendings (records without futures).
- An answer arriving for a parked pending is **held**, not dropped as an unknown correlation.
- Delivery on reattach: when the relaunched agent re-asks in the same conversation (or next touches it), the parked answer is returned instead of a fresh phone round-trip. Spawn/resume flows can surface "an unanswered question was waiting."
- Phone-side result: questions survive restarts. Agent-side result: the answer waits instead of being lost.

## Frequency argument

Deploy restarts are frequent during development but happen at-desk, where the at-desk redirect already applies. Away-mode restarts (crash, reboot) are rare but hit the core use case at its worst moment; today's mitigation (startup clears away mode so agents fall back to terminal output) prevents stuck agents but still loses the questions and any answers in flight.

## Verdict

**Promoted from founding non-goal to scheduled backlog, sequenced after the convening chunks.** Rationale: the failure is real and now poorly aligned with the product's identity, but away-mode restarts are rare, and convening delivers more daily value first. The parked-pendings scope above is the agreed shape for the eventual chunk; the convening design must not foreclose it (checked: the wake matrix's `awaiting_human` rule — append convene notices to the eventual answer payload — works identically with parked pendings).

**Explicitly rejected:** (a) promoting it ahead of convening (no incident pressure justifies the reordering); (b) reaffirming the non-goal (the always-on service and the startup sweep's visible phone-side cancellations make "acceptable degradation" no longer honest).
