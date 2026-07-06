# Convening Chunks 2-5: Roadmap and Decisions

**Date:** 2026-07-06
**Status:** Approved by John (brainstormed 2026-07-06; resolves the parent's "Proposed, not yet brainstormed" status)
**Parent:** `2026-07-01-convening-simplification-design.md` (the full design; this doc records the chunking, the decisions, and the deltas). Companions: `2026-07-06-session-registry-chunk-design.md` (chunk 1, in implementation), `2026-07-06-t001-retriage-decision.md` (T-001 sequenced after these chunks).
**Plans:** deferred by decision - each chunk's implementation plan is written only after the preceding chunk lands, because plans are grounded in exact signatures and the tree changes under them. Specs don't have that problem; this one is stable against chunk 1's *interfaces* (SessionRegistry, `sessions/` tree, registry states, `GET /sessions`).

## Ordering decision (server-first)

- **Chunk 2 - agent path:** `join_conversation` + structured JSON returns + the single SKILL.md protocol revision. Server-only.
- **Chunk 3 - human path:** convene command + dispatcher + wake delivery + Operator multi-select. Live sessions only.
- **Chunk 4 - Android batch:** Sessions board, spawn resume-picker rework, phone convene multi-select, resume-into-conversation. One Gradle/deploy cycle.
- **Chunk 5 - deprecation:** open/enter become logging shims, then the open marker and lobby machinery are deleted; SKILL.md final trim.

Rationale: convening via Operator needs no Android work (multi-select rides on chunk 1's Sessions rail), and during development convening is as much an at-desk operation as a phone one - so the phone board is off the critical path. Server momentum continues directly from chunk 1, convening becomes usable earliest, and all slow-iteration Android work batches into one chunk. Rejected orderings: phone-first (roster on the phone soonest, but convening lands last and Android is touched twice) and convene-first (human path before agent path splits the SKILL.md protocol churn across two windows).

## Chunk 2 - `join_conversation` + structured returns

**The tool:** `join_conversation(sender, ref?)`. Never blocks.

- `ref` absent: if the open marker points at an active conversation, join it; otherwise mint a new conversation, **promote it to the open marker**, and return immediately. The promotion is what preserves today's pairing semantics through the deprecation window: the second ref-absent joiner lands with the first instead of minting a second room. Waiting for a peer stops being implicit in the join: a joiner who wants to wait calls `message_and_await_agent` next (already the blocking verb).
- `ref` given (a conversation_id from `lookup_conversation_ids`, a convene notice, or John's prompt): idempotent join. Already a member → `{"status":"ok","already_member":true}`. Bound to a different conversation → migrate (same explicit-move semantics as enter's Branch 3 today).
- **History is synchronous:** the join response carries the conversation log that `_queue_for_intro` delivers today by blocking. No intro-queue wait exists on the join path.

**Structured returns:** `join_conversation`, `message_and_await_agent`, `leave_conversation`, `combine_conversations`, and `lookup_conversation_ids` return one-line JSON with a `status` field: `ok | timeout | conversation_ended | conversation_empty`, plus `conversation_id`, `peers`, `log`, `cause` as applicable. `ask_human` keeps its bare-string reply for human answers; only its terminal sentinels adopt the JSON shape (`{"status":"timeout"}`, `{"status":"conversation_ended","cause":"force-ended"}`), so sentinel string-matching leaves agent-land in one revision.

**SKILL.md:** rewritten once, in this chunk, documenting only the new surface. open/enter keep working, undocumented, until chunk 5. One plugin version bump covers the SKILL revision.

**Not touched in chunk 2:** open/enter handlers, the open marker, `open_peer_future`, the lobby-hold branches (all die in chunk 5). The wait-queue/talking-stick core is untouched throughout.

## Chunk 3 - convene command, wake delivery, Operator multi-select

**Command tree:** `convene_commands/<push-id>` - top-level, matching `spawn_commands`/`combine_commands`/`away_mode_commands`. (Delta from the parent spec's `commands/convene/` nesting, for consistency with the existing trees.) Shape: `{session_ids: [...], target: "new" | "<conversation_id>", title, issued_at, status}`. New `dispatch_convene_commands` loop, freshness-gated via `command_freshness.py`, supervised like its siblings.

**Per-session routing** (existing `conversation_ops` machinery): unbound → `_add_member` + bind; bound to a solo conversation → `_migrate_member`; bound to a multi-party conversation → **skipped**, reason recorded on the command result. Pulling a member out of an active collab remains `combine_conversations`' job. A system intro message ("John convened: Claude Win, Claude WSL") lands in the target conversation.

**Wake matrix implementation** (decided: extend existing hooks; no new hook registrations):

| Session state | Mechanism | Latency |
| :--- | :--- | :--- |
| `awaiting_agent` | server resolves the blocked `message_and_await_agent` future with `{"status":"convened", conversation_id, peers, log}` | immediate |
| `awaiting_human` | never preempted; convene notice attached to the pending record and prepended to the eventual answer payload | on answer |
| `active` | per-session **notice queue** on the SessionRegistry (RTDB-mirrored; survives restart). Turn-end hook's GET gains `session_id`; the response gains `notices`; the Stop hook blocks with the notice text as reason - the away-mode enforcement mechanism reused | end of turn |
| `idle` | same queue, delivered as `additionalContext` on the next UserPromptSubmit | John's next touch (passive only in this chunk) |
| `ended` / `lost` | not selectable in chunk 3; resume-into-conversation ships in chunk 4 | - |

Notices pop on read (at-most-once). Backstop for a discarded hook response: the convene intro message already sits in the conversation, so the session learns on its next conversation touch regardless.

**Operator:** chunk 1's Sessions rail gains multi-select checkboxes on live rows (`active`/`idle`/`awaiting_*`), a Convene button, a target picker (new conversation + title, or an existing active conversation), and per-row wake-path hints. Writes go through a `commands.js` builder; `node --test` units for the builder and any new derivations.

**Parent open questions, resolved:**

1. *CLI-agnostic convene notices* - scoped to Claude for now. The turn-end hook's `--cli` abstraction stays; Gemini verification is deferred (the repo already de-emphasized Gemini naming).
2. *Force-pull from multi-party conversations* - no. Skip-and-report; combine is the forceful verb. Revisit only if real usage shows repeated convene-then-combine.
3. *Default-room semantics after the marker retires* - keep join-or-create for `ref`-absent joins; it remains the zero-configuration path for ad-hoc two-agent work.

## Chunk 4 - Android batch (sketch; gets its own UI brainstorm before its plan)

One deploy cycle: a Sessions board page (sibling to the conversation board, reading `sessions/`), the spawn sheet's resume picker switching from conversation-member archaeology to registry `ended` records, convene multi-select with wake-path labels (the fork case labeled "Resume into conversation" explicitly), and the resume-into-conversation action itself (spawn-resume with a convene prompt; the forked session's record supersedes the original's). Layout and interaction details deliberately deferred to a short UI-focused brainstorm - possibly with mockups - before that chunk's plan.

## Chunk 5 - deprecation window and deletion

- `open_conversation` / `enter_conversation` become thin shims over `join_conversation` (+ `message_and_await_agent` for enter's blocking expectation), each logging a deprecation event to the JSONL audit log so remaining usage (spawn prompts, habits) can be found and updated.
- After two plugin versions with a quiet deprecation log: delete the shims, the singleton open marker, `open_peer_future`, the mint-path lobby (`_queue_for_open_peer`'s opener arm), and `message_and_await_agent`'s sole-alive lobby-hold branch (sole-alive uniformly returns `{"status":"conversation_empty", ...}`).
- With the marker gone, `ref`-absent join adopts the parent spec's rule: if exactly one active multi-party-capable conversation is accepting joins, join it; otherwise mint. (Resolved open question 3: join-or-create stays; only the mechanism backing it changes.)
- SKILL.md loses the last legacy paragraphs.
- T-001's parked-pendings chunk follows (see the re-triage decision doc).

## Interfaces this roadmap assumes from chunk 1

`SessionRegistry` with per-session state (`active | idle | awaiting_human | awaiting_agent | ended | lost`), the RTDB `sessions/` tree and its Operator rail, `GET /sessions`, and D4's session-keyed identity (pendings keyed by `(conversation_id, cli_session_id)`; answers resolved by request_id). If chunk 1's empirical subagent check (its Task 11) forces roster filtering, the convene UI inherits it for free - selection operates on the roster as rendered.
