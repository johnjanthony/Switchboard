# Session Registry Chunk 1: D4 Identity Refactor + Registry Core + Operator Rail — Design

**Date:** 2026-07-06
**Status:** Approved by John (brainstormed 2026-07-06; supersedes the "Proposed" status of the parent specs for the scope below)
**Parents:** `2026-07-01-session-lifecycle-registry-design.md` (the substrate design this chunk implements), `2026-07-01-architecture-review-goal-drift.md` (findings D1/D2/D4/D6), `2026-07-01-convening-simplification-design.md` (the consumer this chunk unblocks; not implemented here)
**Scope:** Python server + plugin hooks + Operator dashboard + docs. The Android Sessions board, spawn resume-picker rework, and all convening work are explicitly the next chunk(s).

## Decisions made in this brainstorm

These resolve the parent specs' "not yet brainstormed with John" status for chunk 1:

1. **Chunk = server + Operator.** The phone Sessions board follows in a later chunk, matching the proven "Operator first, phone follows" rollout pattern from 2026-06-25.
2. **The D4 identity refactor is folded in, and lands first.** Sequencing is D4 → registry core → sweeper/RTDB/hydration → Operator rail → docs. The registry is built on the clean identity model from day one rather than being migrated after the fact.
3. **No data migration — Firebase is wiped at deploy.** Existing conversations/sessions in RTDB do not carry forward. Hydration and every reader target the new schema only; no old-record tolerance shims, no dual-format handling, anywhere in this chunk.
4. **Parent-spec open questions resolved:**
   - *SessionStart transport* (Q1): fire-and-forget POST only, no marker file. A missed birth self-heals via the MCP-call safety net; a missed death does not, which is why only SessionEnd keeps the marker mechanism.
   - *Subagent sessions* (Q2): stays empirical — an early implementation task spawns a Task-tool subagent and observes what SessionStart/agent-status hooks actually report. The record design is defensive either way; a filter or parent-link is added only if the check shows roster flooding. **Outcome (implementation, 2026-07-06): no roster flooding observed; no filter was needed** (evidenced by the shipped hooks carrying no subagent filter and the plugin staying at 1.1.0 through the live smoke).
   - *Retention* (Q3): 72h as specced. The registry stays short-horizon; long-tail resume remains a Watchtower-assisted flow.
   - *Multiple CLIs* (Q4): `SessionRecord.cli` field added now; only Claude feeds it initially.
5. **Docs corrections D1/D2/D6 ride along** as an independent phase.

## Phase 1 — D4 identity refactor (identity = `cli_session_id`; `sender` = display attribute)

The conversation `Registry` currently runs two identity systems: rosters keyed by disambiguated sender, pendings keyed by raw agent-supplied sender, with `cli_session_id` carried as a correction factor. This phase collapses them to one.

- **`Conversation.members_active`** rekeys `dict[sender, ConversationMember]` → `dict[cli_session_id, ConversationMember]`. `sender` remains a field on the member. Sender-collision disambiguation (`Claude Win` → `Claude Win 2`) still runs at join time but now uniquifies a display label; it no longer mutates an identity key, and the raw-vs-disambiguated divergence stops mattering for routing.
- **`Registry._pending`** rekeys `dict[(conversation_id, sender)]` → `dict[(conversation_id, cli_session_id)]`, with `cli_session_id` required (the PreToolUse injector hook guarantees it on every switchboard call; calls without it are already rejected). Supersede semantics are unchanged: a new ask from the same session in the same conversation cancels and replaces the prior one.
- **Answer resolution moves to `(conversation_id, request_id)`.** The phone's answer payload is `{text, sender, request_id, written_at}` written at `conversations/<conv_id>/answers/<request_id>` — `request_id` is already in both the path and the payload and is unique per question. `dispatch_responses` resolves the pending whose `request_id` matches within the conversation; the `sender` field in answers becomes display-only. The T-148 stale-answer guard becomes inherent: a request_id match *is* the guard, so the separate mismatch check disappears. **No Android change and no wire-format change.**
- **Correlation reduces to `conversation_id`.** The answers listener already extracts `conversation_id` from the event path and carries `request_id` in its own field on `IncomingResponse`; with resolution keyed by `(conversation_id, request_id)`, the `(conv_id, sender)` correlation tuple has no remaining job. `dispatch_responses` resolves directly from `conversation_id` + `request_id`; the sender payload field is used for logging/display only. The legacy `(cwd, sender)` correlation from the old `responses/` tree (`firebase.py` legacy poll path) is checked for real usage; the phone writes only to the `answers/` path today, so the legacy path retires in this phase unless the check proves otherwise.
- **No compatibility window.** Pending futures die at the deploy restart regardless (T-001), and Firebase is wiped, so the correlation/keying shape changes atomically.
- **Cleanup paths simplify.** `cancel_stale_pending_for_conversation` and session-end cleanup already match by `cli_session_id`; after the rekey that is the primary key lookup, not a fallback scan of value fields. The "pending with no known owner is stale" branch disappears — every pending has an owner by construction.
- The RTDB conversation projection (member display data, message attributions) keeps sender-based display fields; it is a display view. Persisted member records already carry `cli_session_id`, and post-wipe hydration reconstructs the session-keyed in-memory maps from the new-schema records only.

## Phase 2 — SessionRegistry core

- **New `server/session_registry.py`:** `SessionRecord` as specced in the parent (identity `cli_session_id`, canonical `cwd`, `surface`, `started_at`, `last_event_at`, `state`, `state_detail`, `conversation_id`, `sender`, `model`, `context_pct`, `end_reason`, `source`) plus the `cli` field. `SessionRegistry` lives alongside the conversation `Registry` — same single-event-loop access model, no locking.
- **State values:** `active | idle | awaiting_human | awaiting_agent | ended | lost`, exactly as specced. (The parent spec's hydration mention of `state=unknown-age` is a spec bug — it is not in the Literal. Resolution: records rehydrate with their persisted state and honest `last_event_at`; the sweeper judges from there. No new state.)
- **New plugin hook:** `SessionStart` → `scripts/cli-session-start-hook.py`, fire-and-forget POST to new route `POST /session_start` (payload: `session_id`, `cwd`, hook `source` = startup/resume/clear), same shape and timeout discipline as `agent-status-hook.py`. `hooks.json` gains the event; the plugin version is bumped (plugin cache staleness lesson).
- **Event wiring** per the parent's state-machine table:
  - `/agent_status` upserts a minimal record for unknown session ids instead of dropping them — the single change that makes sessions first-class. UserPromptSubmit → `active`; PreToolUse of ask_human → `awaiting_human`; PreToolUse of message_and_await_agent → `awaiting_agent`; PostToolUse/Stop → `active`/`idle` (existing CLEAR_TOOLS/WAITING_TOOLS mappings).
  - SessionEnd marker sweep → `state=ended` + `end_reason`.
  - `spawn.py` upserts a provisional record (`source=spawn`) at launch, reconciled when the session's own SessionStart arrives.
  - Widget-snapshot ingest bumps `last_event_at`, enriches `model`/`context_pct`, and discovers unknown rings (`source=rings`).
  - Any switchboard MCP call upserts/refreshes (safety net for pre-rollout sessions).
- **Binding consolidation:** `bind_session`/`unbind_session` write `SessionRecord.conversation_id`; `session_to_conversation_id` becomes a view over the registry records rather than an independent dict. One source of truth from day one. (`session_home_conversation_id` fallback semantics are unchanged in this chunk.)

## Phase 3 — Staleness sweeper, RTDB fan-out, hydration

- **Sweeper:** periodic task under the existing `LoopSupervisor` machinery. Marks `lost` only when all three hold: no hook event for `SESSION_LOST_AFTER_SECONDS` (default 900), state is not `awaiting_human`/`awaiting_agent` (a blocked pending future is liveness proof), and the session's ring is absent from a *fresh* Watchtower snapshot. If Watchtower itself is stale/offline, staleness judgments suspend and the roster shows the sensor state instead of guessing. `lost` is reversible — any later event revives the record.
- **RTDB:** new top-level `sessions/<cli_session_id>` tree mirroring the record (fields as specced), written on-change only with the same diff discipline as the widget-snapshot store. Session ids are RTDB-safe as-is; the canonicalization encoder is the fallback if that assumption ever breaks.
- **Retention:** sweeper prunes `ended`/`lost` records older than `SESSION_RETENTION_HOURS` (default 72) from both memory and RTDB.
- **Hydration:** on startup, `sessions/` entries in non-terminal states rehydrate as records with persisted state and `last_event_at`, immediately subject to the sweeper. New schema only (Firebase wiped at deploy — decision 3).
- **`/stats`:** gains a sessions roll-up (counts by state) so Watchtower can later drop its own scan-derived session list. `/healthz` reports the sweeper under the existing loop-supervision block.

## Phase 4 — Operator Sessions rail

- New rail in `dashboard/` reading `sessions/`: path builder in `schema.js`, listener + projection in `store.js`, pure derivations in `derive.js` (state chip, project = cwd tail, ring %, relative age, sensor-offline), new `SessionsRail` component. Rows deep-link to the bound conversation (`#conv` pattern) when bound.
- Rendering follows the existing component idiom; the rail is independently collapsible like the existing rails.
- `node --test` units for every pure-module addition (schema, derive, store), matching the existing test files.
- No write actions from the rail in this chunk (connect/convene/resume actions belong to the convening chunk).

## Phase 5 — Docs corrections (review findings D1/D2/D6)

- **D2:** reframe `CLAUDE.md` (and the README's positioning) from "exists specifically for away mode" to the mission-control-hub identity, with away-mode question routing as the founding feature.
- **D1:** reposition "locally-hosted" as "local gateway, cloud-synchronized state"; record the principle change so future designs argue from the real constraint.
- **D6:** record the LocalSystem service identity as a decided architectural constraint (server cannot read `~/.claude/projects`, cannot reach WSL; registry is push-fed only) — a short decided section in `CLAUDE.md`, not a rediscovered surprise.

## Testing

- **pytest, in-process, mocked backend** (repo convention) for: the D4 rekey (member/pending/resolution paths, supersede, cleanup), registry event wiring and state transitions, sweeper rules (including the Watchtower-offline suspension), RTDB diff-write behavior, hydration, `/session_start` route, `/stats` roll-up. Sweeper/async tests respect the loop-pump gotcha (multiple `sleep(0)` yields or `wait_for(shield(...))`).
- **node --test** for the Operator pure modules.
- **Live smoke at the end:** wipe Firebase, restart service (`-SkipTests`), `/healthz` green, a real Claude Code session (post plugin bump) appears in the Operator roster via SessionStart, ask_human round-trips from the phone, SessionEnd marks it ended.
- **Empirical check task (early):** subagent hook behavior (decision 4/Q2).

## Out of scope (this chunk)

Android Sessions board; spawn resume-picker rework; all of the convening simplification (join_conversation, structured returns, convene command/dispatcher, roster multi-select); T-001 pending-question persistence; cross-host anything.
