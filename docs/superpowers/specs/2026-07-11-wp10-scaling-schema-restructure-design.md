# WP-10 — Scaling/Cost Restructure (REV-107, REV-405 root-sync, DT-9)

**Date:** 2026-07-11
**Status:** Approved (design review with John, 2026-07-11)
**Findings:** `docs/2026-07-08-technical-review-findings.md` — REV-107, REV-405 first bullet (root-sync), DT-9. Folds the "Incidental dead-code sightings" item for the legacy `/responses` node (`send_resolution_confirmation`).
**Grounding basis:** develop @ `a54862d` (post-WP-8). File:line anchors below refer to that tree.

## Problem

Three hot RTDB listeners scale with total history ever, not with what they need:

1. **Server** (`server/firebase.py:1150-1156`): the `conversation_answers` SupervisedListener subscribes to the entire `conversations` tree over SSE to catch `<conv_id>/answers/<request_id>` puts. Every message/meta/unread/status write on any conversation streams back through it, and every connect/reconnect downloads the whole tree as the initial snapshot (REV-107).
2. **Operator** (`dashboard/store.js:311-323`): `onChildAdded`/`onChildChanged`/`onChildRemoved` on the `conversations` root — each child event delivers that conversation's full subtree (all messages) to read `.meta`; initial sync downloads everything (REV-405 root-sync).
3. **Android/wear** (`android/shared/.../MainViewModel.kt:493-580`): a whole-tree `ValueEventListener` on `conversations` re-parses the entire tree on every write anywhere, to build list rows from `meta`/`members_active`/`agent_status`/`unread_count`/`pending_responses` (T-175, the client sibling).

Two structural facts drive the fix: **messages are the only unbounded data inside a conversation**, and **ended conversations are never deleted** (only session records have a retention sweep), so even bounded per-conversation nodes accumulate forever at the root.

Neither RTDB SDK can enumerate children without syncing them, so "narrow the listener" requires moving the data, not changing listener flags.

## Decisions (from the design review)

- **Approach B — relocate the heavy data.** Messages move to a top-level `/messages/<conv_id>` node; answers move to `/answers/<conv_id>`. The `conversations` tree becomes the bounded index card every list surface already parses. Rejected: (A) answers-only move — leaves REV-405/T-175 unfixed, under-delivers DT-9; (C) `/conv_index` mirror node — creates a second source of truth maintained by fire-and-forget writes (exactly the DT-5 disease), doubles meta writes, and forces clients' `hidden`/`unread` writes to dual-target or drift.
- **Retention: 72h for ended conversations**, matching the session-retention horizon. Rationale: session records already prune at 72h and phone Resume needs them, so an ended conversation older than 72h is already non-resumable; keeping it costs sync forever for zero capability.
- **Wear:** a watch exists but is rarely used — update it opportunistically after the cut; brief breakage acceptable. Wear inherits all path changes via the shared `MainViewModel`; the gate for this WP is compile + shared unit tests.
- **Legacy `/responses` node machinery is deleted in this WP** (not deferred to dead-code pass 2): we are rewriting exactly that path.
- **REVISED 2026-07-11 (John, post-plan): no data migration.** The one-shot startup migration originally specified below was dropped. Instead John purges the RTDB `conversations` and `responses` top-level nodes at cut time, between service stop and start. Rationale: 72h-retention data with no testing value; unmigrated old data would keep bloating the index card indefinitely (the sweep only touches ended conversations); a purge is simpler than idempotent move machinery. Accepted cost: the phone-browsable history of the last ~72h is discarded at the cut.

## Target schema

```text
/conversations/<conv_id>/        <- bounded "index card" (hot: all list surfaces)
	meta/ {title, state, created_at, last_activity_at, ended_at, hidden, preview, continued_from, origin}
	members_active/<sender>/...
	members_history/<sender>/...
	pending_questions/<request_id>/...
	agent_status/<sender>/...
	unread_count                   (int; server increments, phone resets)
	pending_responses              (int mirror; live reader = Android badge displayCount)

/messages/<conv_id>/<push_id>    <- unbounded history (cold: watched per-conv while viewing)
/answers/<conv_id>/<request_id>  <- transient answers (phone/Operator write, server consumes+deletes)
```

What deliberately does NOT move:

- `pending_questions` — bounded (records deleted on resolve/terminate); both clients already watch it via narrow per-conversation listeners (WP-6 Option B on Android, `syncPendingListeners` on Operator); the parked-pendings hydration (T-001) reads it in place.
- `members_history` — bounded by departed-member count; hydration is its only reader (the Android `left_at` read from `members_active` is the known dead read).
- `unread_count`, `pending_responses`, `meta/hidden` — existing narrow client write sites stay exactly where they are.

## Server changes

- **Answers listener** (`firebase.py:1084-1158`): subscribe to `/answers`. Incremental put paths parse as `<conv_id>/<request_id>` (2 segments, was 3). The H06 reconnect-replay branch (empty event path) walks the small `/answers` snapshot: `{conv_id: {request_id: entry}}`. Slot format stored in `IncomingResponse` becomes `answers/<conv_id>/<request_id>` and `delete_response_slot` deletes that path directly.
- **Message write/read sites** repoint from `conversations/<id>/messages` to `messages/<id>`: the push sites (`firebase.py:828`, `:892`), the message get (`:428`), and the `msgs_ref` use at `:201`. An implementation-time grep for `conversations/{` and `"/messages"` must sweep firebase.py for any site this list missed.
- **Legacy `/responses` machinery deleted**: `_resp_ref` (`firebase.py:98`), `send_resolution_confirmation` (`firebase.py:479-490`) and its call sites, and `delete_response_slot`'s legacy else-branch (`firebase.py:492-500`). The messenger ABC / protocol surface loses the method; tests and fakes follow.
- **Hydration** (`server/hydration.py`): step 2 reads `/conversations` (now bounded); for each hydrated **active** conversation, a per-conv read of `/messages/<id>` supplies the live-log subset (`_LIVE_MESSAGE_TYPES` filter unchanged, push-key sort unchanged). Parked-pendings step (2b) unchanged — `pending_questions` did not move.
- **Retention sweep** (new): a periodic loop in the dispatch family with the same supervision/reporting pattern as the session sweep. Cycle: shallow-get `/conversations` ids, read each id's `meta`, and for every conversation with `state != "active"` whose `ended_at` (fallback `last_activity_at`) is older than the horizon: delete `/conversations/<id>`, `/messages/<id>`, `/answers/<id>`, and evict the id from the in-memory registry if present (guarded: only when the in-memory copy is also ended). Active conversations are never touched regardless of age. Cadence: hourly (`interval` kwarg like the session sweep's, default 3600s — a 72h horizon does not need the session sweep's 60s tick, and the whole WP is about cutting RTDB chatter); the loop body runs a pass immediately at startup before its first sleep, so no separate first-pass wiring is needed. Config: `SWITCHBOARD_CONVERSATION_RETENTION_HOURS`, default 72, on `Config` next to `session_retention_hours`. JSONL-log each sweep that deletes anything.
- **One-shot migration — DROPPED (see the REVISED decision above).** No migration code ships. Old-schema data (including the legacy `/responses` node) is removed by John's manual purge at cut time; the new server starts against an empty tree and hydrates nothing.

## Client changes (path constants only; zero listener-shape changes)

- **Android/wear shared `MainViewModel.kt`**: per-conv messages listener attach/detach (`:655`, `:675`, `:187`) → `messages/$convId`; the `opened` flag write (`:769`) → `messages/$convId/$msgId/opened`; the answer write (`:780`) → `answers/$convId/$requestId`. The whole-tree conversations listener (`:493-580`) is untouched — it is what becomes cheap. Wear consumes all of this via shared.
- **Operator**: `dashboard/schema.js` `messages(id)` → `` `messages/${id}` ``; `dashboard/commands.js` answer path → `` `answers/${convId}/${requestId}` ``; `store.js` listener wiring unchanged; `schema.test.js`/`commands.test.js` assertions updated. doc-view/document pills go through the `/document` HTTP proxy, so they are expected untouched — verify at plan time.

## Rules + deploy cut (John-assisted)

- The **deployed** RTDB rules must grant the same access to the new top-level `/messages` and `/answers` nodes as `conversations` has today. The repo's `database.rules.json` is a PLACEHOLDER — never deploy it. The rules change is additive, so it deploys safely BEFORE the cut.
- Cut sequence: (1) rules deploy, (2) stop the service (`nssm stop switchboard`), (3) **purge**: delete the RTDB top-level `conversations` and `responses` nodes (optionally `cli_sessions` — hydration skips stale home pointers either way; leave `sessions`, `session_acks`, `global_settings`, `widget`, and the command nodes alone), (4) start the service (restart severs MCP sessions; known cost), (5) refresh the Operator tab, (6) `install-client.ps1` for the phone, (7) wear opportunistically later. The purge sits between stop and start so neither the old server (re-materializing from in-memory state) nor the new one (hydrated ghosts) races it.
- Old-client window between (4) and (6): the purge already emptied the old app's list; an answer sent from the old app lands in the abandoned `conversations/<id>/answers` path and is DROPPED (nothing listens there anymore) — don't answer questions from the phone until step (6) is done.

## Testing

- **pytest** (fake backend follows the new paths): answers-listener parse (2-segment incremental puts; snapshot replay from `/answers`), sweep (horizon math incl. `ended_at` fallback, all-three-node delete, registry eviction guard, active-never-touched), hydration split-read (active conv messages come from `/messages/<id>`; live-log filter preserved). Existing suite (837 collected at plan time) is the regression floor. `--basetemp="$LOCALAPPDATA/Temp/sb-pytest"`.
- **node --test** `dashboard/*.test.js`: schema/commands path assertions.
- **Android**: `:shared:testDebugUnitTest` + `:app`/`:wear` `compileDebugKotlin` with JDK 21 (never bare `gradlew build` — pre-existing `:wear:lintDebug` failure).
- **Live smoke at deploy** (John): phone answer round-trip resolves an `ask_human`; Operator answer send; a new message visible in both detail panes; sweep JSONL line after first cycle; `/healthz` shows `conversation_answers` live on the new path.

## Out of scope

- `pending_responses` mirror reconciliation (DT-5) — the counter survives unmoved with its Android badge reader.
- Dead-code/DRY pass 2 items not on this path (T-223/T-225), except the `/responses` fold above.
- Watchtower — HTTP-only (`/stats`, `/widget-snapshot`), unaffected.
- FCM payloads — conv/msg ids only, unaffected.
- SKILL.md / hooks — no RTDB paths in either; expect NO plugin bump (plan-time grep to confirm).

## Tracking on land

Add a `WP-10` ledger row; move **T-190** (answers-listener-on-root, the REV-107 backlog twin) to the ledger; re-check **T-175** (Android whole-`/conversations` value listener — the root listener remains but its cost driver is removed; close or annotate accordingly); annotate the findings-doc WP-10 row.
