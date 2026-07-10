# WP-3: Binding/Membership Invariants + Hydration — Design

**Date:** 2026-07-09
**Findings:** REV-103, REV-104, REV-110, REV-112, REV-113, DT-2, DT-4 (`docs/2026-07-08-technical-review-findings.md`)
**Status:** Approved by John 2026-07-09 (brainstorm rounds; all recommended options ratified). Implementation plan to follow (`docs/superpowers/plans/`).

## Problem

The session→conversation routing map (`registry.session_to_conversation_id`) is rebuilt on restart exclusively from alive members of Active conversations (`server/hydration.py:157-164`). Two code paths bind sessions to conversations that have no member — session-fallback's `create_new` arm (`server/session_fallback.py:91-119`, REV-112) and spawn's `handle_fresh`, which binds before the member exists (`server/spawn.py:366-380`, the member appears only at the agent's first MCP call, REV-103). Those bindings silently vanish at hydration, and live state and post-restart state disagree (DT-2). Around the same machinery: member migration mirrors to Firebase as 2-3 independent fire-and-forget writes (REV-104), a ref-less `join_conversation` from a bound session returns a false `minted=true` or gets hijacked by the join-candidate rule (REV-110), spawn persists the home pointer only on the mint branch (REV-113), and hydration reloads every persisted message type into `conv.messages` while live code appends only a subset (DT-4).

## Grounding discoveries (context for the decision)

- **A persisted per-session binding value already exists.** `Registry.bind_session`/`unbind_session` mirror onto the SessionRegistry record (`server/registry.py:138-147` → `session_registry.py:242-249`), which persists whole to RTDB `sessions/<cli_session_id>` including its `conversation_id` field, and hydration reloads it into the SessionRegistry — but the routing-map rebuild ignores it. There is no dedicated binding node; the findings doc's "bindings are not persisted" is imprecise in that one respect.
- **The alive-member derivation is deliberate.** `hydration.py:149-156` documents the "dormant = unbound" steady-state invariant (H03/M21): re-binding dormant members at hydration used to break phone Resume permanently. Any fix must preserve dormant-stays-unbound.
- **The atomic primitive for REV-104 already exists.** `db.reference().update({multi-path dict})` is used by `reset_all_pending_responses` (`server/firebase.py:298-305`) and nowhere else.

## Decisions (ratified 2026-07-09)

1. **DT-2: enforce "bound implies member"; keep hydration's alive-member derivation.** Rejected: rebuilding the routing map from the persisted `sessions/<id>.conversation_id` records (rewires the deliberate dormant-unbound behavior, needs staleness filtering, bigger blast radius); rejected: doing both (reconciliation half is YAGNI once the invariant holds).
2. **REV-103: patch the consumers; keep the lazy member.** The spawn window (bind at `spawn.py:368`, member on first MCP call) survives, but force-end and the T-145 guard learn to handle bound-but-memberless sessions. Rejected: an eager placeholder member at spawn time (rename mechanics, placeholder visible on every member.sender consumer, real complexity for a seconds-wide window). **Accepted residual:** a crash inside the window orphans an empty spawn conversation (rare, visible on the phone, force-endable).
3. **REV-110: a ref-less join from a bound session rejoins the bound conversation.** Identical to calling join with `ref=<bound id>`: ensure membership, honest envelope, no candidate-rule consultation. Rejected: a new `already_bound` status (new envelope every agent must learn, plugin bump, and the spawned agent's reflexive join would need a second call to collect history).
4. **REV-104: both halves — atomic multi-location move AND a hydration reconciliation pass.** Rejected: atomic-only (pre-existing or other-cause splits keep resolving silently by iteration order forever); rejected: reconcile-only (every crash re-creates the corruption).

## Design

### 1. The invariant + REV-112: fallback mints with a member

`apply_fallback`'s `create_new` arm (`session_fallback.py:91-119`) currently constructs a bare `Conversation` inline, binds the session, and persists meta + home — never a member. It gains the member:

- Construct a `ConversationMember` for the session: `sender` and `cwd` pulled from the SessionRegistry record (`registry.sessions.get(session_id)`), defaulting to `"Claude"` / `""` when no record exists; `surface` via `_infer_surface(cwd)`; `alive=True`, `last_seen_seq=0`, `joined_at=now`.
- Insert into `new_conv.members_active` before binding.
- Add a third `_spawn_bg` Firebase write: `backend.write_conversation_member(new_id, member)` — this is the crux; it is what lets hydration's alive-member derivation rebuild the binding after restart.
- The function stays synchronous (`_spawn_bg` mirrors), matching its current shape.

After this, every `bind_session` call site in the codebase either has a member in place or is one of two transient, self-healing exceptions: the spawn window explicitly handled by section 2, and `apply_fallback`'s `rebind_home` arm (which may rebind a session to a home conversation it is no longer a member of — the member is re-added by `_resolve_conversation_and_member` on the session's next MCP call, and a restart inside the window drops the binding cleanly with no orphan state).

### 2. REV-103: spawn-window consumers

- **Force-end** (`server/gateway/dispatch.py:206-265`): the fallback set becomes the union of `conv.members_active.keys()` and every `sid` where `registry.session_to_conversation_id[sid] == conversation_id`. Bound-but-memberless sessions (the spawn window) now get `apply_fallback` — with away mode on (spawn auto-enables it) and the home pointing at the just-ended conversation, `compute_fallback` yields `create_new`, which per section 1 now mints WITH a member, so the spawned agent's first MCP call routes into a live, hydration-safe conversation.
- **T-145 guard** (`server/gateway/handlers.py:243-252`): when it fires (session bound to an Ended conversation), it applies fallback (`apply_fallback(registry, cli_session_id, backend=backend)`) and still returns the `conversation_ended` sentinel — truthful for this call, correctly routed for the next. It no longer leaves the stale binding leaked.

### 3. REV-110: ref-less join from a bound session

In `join_conversation` (`handlers.py:787-804`), the ref-less path becomes:

- **Bound + conversation Active:** short-circuit `target_id = bound_id`. The candidate rule is never consulted (the hijack of freshly-spawned agents is dead by construction). The existing downstream branch (`bound_id == target_id`) handles ensure-membership and returns the honest envelope: `minted=false`, real peers, wake payload/log.
- **Bound + conversation Ended/missing:** apply fallback first (same self-heal as section 2), then re-read the binding: rebound (home) → rejoin that conversation; unbound → fall through to today's candidate/mint path. Because the stale binding was just cleared, `_create_active_conversation_for`'s short-circuit (`conversation_ops.py:89-92`) can no longer return a stale id — the mint branch genuinely mints, and `minted=true` becomes truthful.
- **Unbound:** unchanged (candidate rule, else mint).

`_find_join_candidate` itself (`conversation_ops.py:49-63`) needs no signature change — bound callers never reach it.

### 4. REV-113: home persist on both spawn branches

In `handle_fresh` (`spawn.py:366-380`), the Firebase home write is currently nested inside `if not join_existing:` while the in-memory `set_session_home` runs unconditionally when `home_newly_set`. Drop the `join_existing` gate: persist whenever `home_newly_set`, with a comment stating the invariant — an in-memory home-pointer mutation is always accompanied by its Firebase persist (`cli_sessions/<id>/home_conversation_id`).

**Observed, out of scope:** `handle_resume` never sets a home pointer for resumed sessions (neither memory nor Firebase); their home continues to point at the ended source until the dormant-cleanup path clears it. Not a findings item; left unchanged and noted here.

### 5. REV-104: atomic member move + hydration reconciliation

**Atomic move.** New `FirebaseBackend` method (name indicative) `move_conversation_member(source_id, target_id, member, old_sender, *, end_source: bool = False)` composing one `db.reference().update({...})` multi-location write: delete `conversations/<source>/members_active/<old_sender_key>` (None value), set `conversations/<target>/members_active/<new_sender_key>` payload, and optionally `conversations/<source>/state = "ended"`. Sender-to-key encoding reuses the same canonicalization the existing per-node writes use. Rerouted call sites (each currently 2-3 independent `_spawn_bg` writes):

- `_migrate_member` (`conversation_ops.py:312-325`)
- `_perform_combine`, alive arm (`conversation_ops.py:459-467`) and dormant arm (`:489-497`)
- `handle_resume`'s member-move loop (`spawn.py:539-547`) — same unordered-pair pattern, same fix

A no-op default on the `ConversationStore`/messenger ABC keeps test fakes working.

**Reconciliation.** In hydration, after conversations/members are built and BEFORE the binding derivation (`hydration.py:157-164`): build `cli_session_id → [(conv_id, member)]` over alive members of Active conversations; where a session appears more than once, keep the copy with the later `joined_at` (deterministic tiebreak on `conv_id` string ordering for exact ties), demote every loser to that conversation's `members_history` (memory) and mirror to Firebase (`remove_conversation_member` + `write_conversation_member_history`), logging a loud `surface_error` naming both conversations. The binding derivation then sees exactly one alive member per session.

### 6. DT-4: one definition of `conv.messages`

Live code appends only three message types to `conv.messages`: `system`, `agent_msg`, `parting` (all append sites verified). Hydration (`hydration.py:252-256`) reloads every persisted type — including `question`, `human`, `notify`, `document`, which never enter the live list — so `len(conv.messages)`, wake-payload deltas, and `last_seen_seq` cursor domains change across a restart.

Fix (the finding's stated direction — make hydration match live behavior): hydration filters the sorted Firebase messages to `type in {"system", "agent_msg", "parting"}` before extending `conv.messages`. Post-restart the list has the same shape as the live list; cursors recorded before a restart mean the same thing after it. Phone and Operator read Firebase directly and are unaffected.

## Constraints

- Server + tests only, plus: the repo `CLAUDE.md` conversation-model paragraph gains "already-bound callers rejoin their bound conversation" next to the candidate-rule description. SKILL.md: the plan-writing grounding must check whether its `join_conversation` text describes the old ref-less behavior — if a touch-up is needed it carries the `.claude-plugin/plugin.json` version bump; if not, no bump.
- No new datastore; no schema change beyond writes to existing node shapes (the multi-location update writes the same nodes the individual writes touch today).
- Preserve: dormant-stays-unbound at hydration (H03/M21); `apply_fallback` stays synchronous; the candidate rule's semantics for unbound callers; force-end's existing pending-termination and member-clearing behavior.
- Testing: pytest throughout — hydration reconciliation and DT-4 filtering are pure-function-testable; the atomic move asserts on the single composed update dict (fake backend records it); force-end/T-145/join behaviors via the existing handler-test idioms. Suite baseline at plan-grounding time governs expected counts.

## Out of scope

- REV-115 (convene orphan on all-failed launch) — WP-4 territory per the findings table.
- `handle_resume` home-pointer behavior (noted in section 4).
- Consuming the persisted `sessions/<id>.conversation_id` at hydration (rejected alternative, recorded above).
