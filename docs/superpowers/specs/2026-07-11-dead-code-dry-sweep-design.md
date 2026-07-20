# Dead-code / DRY second-pass sweep — design

**Date:** 2026-07-11
**Status:** Approved (John, via phone during away-mode run, 2026-07-11)
**Inputs:** backlog T-223, T-224 (residual), T-225, T-232, T-233, T-144; `docs/2026-07-08-technical-review-findings.md` "Incidental dead-code sightings"; WP-6/WP-7 deferred cleanup items
**Predecessor:** the technical-review remediation WPs (WP-1..WP-10, all landed by 2026-07-11). This chunk is the review's deferred second pass ("dead code / unused deps / DRY — run it after the HIGHs land") plus the accumulated tracked cleanup items.

## Why now

All 10 remediation WPs have landed; this is the last open remediation work. The WP work itself removed or revived several symbols the 2026-07-01 review flagged, so every claim below was re-verified against the current tree (post-`828058c`) on 2026-07-11 before deciding scope.

## Re-verification ledger (current tree, 2026-07-11)

### Already resolved by WP work — no action

| Claimed item | Current state |
|---|---|
| `send_resolution_confirmation` (findings doc) | Deleted by WP-10. Zero hits. |
| `onNewIntent` dead in both Android activities (findings doc) | Now LIVE — WP-6/REV-005 adopted singleTop; both activities' `onNewIntent` are real entry points. Must NOT be deleted. |
| T-169 leave/end DRY copy | Obviated by convening chunk 5 (`0580778`); already in the completed ledger. |
| T-144 join-wake × 3 copies | Obviated by the convening wake rework (chunks 3/5, WP-9): `open_peer_future` and the `ok. open_conversation =` payload have ZERO hits in the current tree; the wake path is now the single `_wake_one_from` (conversation_ops.py:582) + `_compose_wake_payload` (200) pair. Cut to ledger as obviated; no task. (Found AFTER the D4 approval, which had included a T-144 fold — the fold is moot.) |
| T-224 Part A layout omissions | Mostly fixed by WP-7. Residual: `server/claude_status.py` and `server/widget_snapshot.py` still missing from CLAUDE.md Layout. |

### Confirmed dead — server (zero production references)

| Symbol | Anchor | Notes |
|---|---|---|
| `config._require()` | `server/config.py:76` | Zero call sites anywhere. |
| `JsonlLogger.collab_message_sent` / `collab_message_received` | `server/logging_jsonl.py:154/162` | Zero callers, including tests. |
| `JsonlLogger.spawn_failed` | `server/logging_jsonl.py:139` | Zero callers, including tests. |
| `FirebaseBackend.send_spawn_ack` + `MessageWriter.send_spawn_ack` ABC entry | `server/firebase.py:547`, `server/messenger.py:80` | No production caller; tests only mock it (`test_e2e_spawn_resume.py:27`, `test_spawn_handler.py:20`) and the contract test enumerates it. |
| `set_conversation_hidden` + the whole `ChannelLifecycle` ABC | `server/firebase.py:232`, `server/messenger.py:191` | Hiding is client-side: Operator writes `conversations/<id>/meta/hidden` directly (WP-7 smoke-verified), Android hides locally. ABC is inherited by `_ToolHandlersBackend` (`gateway/handlers.py:156`), `_SpawnBackend` (`spawn.py:99`), and test stubs — base-class lists shrink with the deletion. |
| `write_combine_command` | `server/firebase.py:642` | Combine flows phone → Firebase → dispatch; server-side writer is test-only (`test_firebase_paths.py:115`). |
| `Registry.global_away()` method | `server/registry.py:326` | Test-only (`test_registry.py:164-166`). Live accessor is the `global_away_mode` property; `update_global_away_cache` stays (production-live). |
| `POST /cli-session/end` route | `server/main.py:65, 811-816` | No test exercises the route (the REV-109 rate-limit tests use synthetic `/a`,`/b` routes); tests call `handle_session_end` directly. The marker-file sweep is the real SessionEnd path. CLAUDE.md's "retained for manual/testing use only" claim is false (T-224 Part B). |
| `to_firebase_key` / `from_firebase_key` | `server/canonicalization.py:96/112` | **New since T-223:** WP-10 deleted the legacy response-slot parser — their last production caller. Now referenced only by `canonicalization.py` itself, tests, and a contingency docstring at `server/firebase.py:617-620`. |
| `canonicalize_cwd` + `CanonicalizationError` | `server/canonicalization.py:19/27` | Production-dead but KEPT (Decision D2): T-026 (in-progress) builds on it. Note: T-026 Option A's `/mnt/` handling is already implemented and tested in this module (`_WSL_MOUNT_PREFIX`, line 24), but nothing wires `canonicalize_cwd` into any display path — utility built, unplugged. |

`JsonlLogger.spawn_invalid_path` is NOT dead-listed: it is the natural sink for spawn-validation rejections and gets WIRED IN (see Scope), per T-223's own note.

### Confirmed dead — Android

| Symbol | Anchor | Notes |
|---|---|---|
| `adminListener` field | `MainViewModel.kt:140, 255` | Write-only; listener cleanup goes through `subscriptions.add {...}` (line 256). The admin_notifications LISTENER itself is live on all three surfaces — only the stored field is dead. |
| `isQuestionType` | `MainViewModel.kt:290-292` | Zero callers. The lone inline duplicate of its predicate (`routeConversationMessage:730`) exists only to populate the dead `requestIdToConvId` map — deleting the map deletes that block, so `isQuestionType` is DELETED too (correction to the brainstorm's "call it there" idea: no call site survives). |
| `requestIdToConvId` map | `MainViewModel.kt:124, 731, 793` | Write-only (written, removed, never read). Comment blocks at 121-123 and 728-729 falsely claim `submitReplyForConversation` consumes it (it takes `convId` as a parameter). Delete map + comments. |
| `leftAt` read from `members_active` | `MainViewModel.kt:523`, `network/Models.kt:79` | Server writes `left_at` only under `members_history`, so the read is always null; no consumer renders it. Delete field + read. |

### Confirmed live targets (DRY / quality / observability)

| Item | Anchor | Notes |
|---|---|---|
| away_mode/status_request listener triplets | `server/firebase.py:462-538` | Near-verbatim ~40-line copies bypassing `_start_command_listener` (firebase.py:911). T-225. |
| `_now_iso()` × 3 production defs | `conversation_ops.py:577`, `gateway/handlers.py:71`, `session_registry.py:33` | Plus 2 test copies. T-225 said two; a third accreted. |
| T-232 malformed-answer silent drop | `server/firebase.py:1051-1055` | `_enqueue_answer` returns silently on non-string text/sender. Runs on the Firebase listener thread — the logging fix must bounce via `call_soon_threadsafe` with per-iteration binding (F-75(b) lesson). |
| T-233 cosmetics (a)-(d) | see backlog | Incl. `submitReplyForConversation` KDoc (MainViewModel.kt:772-778) still describing the WP-10-deleted `/responses` slot. |
| ConversationViewScreen answered-set recompute | `ConversationViewScreen.kt:78-82` | Recomputes locally what the row already carries as `answeredQuestionMsgIds` (derived at `MainViewModel.kt:322`). Consume the row field; verify derivation equivalence first (screen derives over display messages, helper over sortedRaw). |
| `locallyAnswered` unbounded growth | `MainViewModel.kt:139, 716` | The `retainAll` predicate only drops ids that REAPPEAR as new pendings (re-ask case); caught-up ids are retained forever. Bound it (prune ids no longer pending in any conversation, and/or prune on conversation detach). |
| spawn_invalid_path wiring | `server/spawn.py:272-273, 308` | Validation rejections currently log via generic `surface_error`; wire `JsonlLogger.spawn_invalid_path` in. |
| CLAUDE.md doc fixes | CLAUDE.md:32, 192 + Layout | Route-claim removal (with D1) + add `claude_status.py` / `widget_snapshot.py` to Layout. |

## Decisions (approved 2026-07-11)

**D1 — Delete the `POST /cli-session/end` route (T-223 + T-224 Part B resolved together).** The marker-file sweep is the real SessionEnd path; no test exercises the route; the "retained for manual/testing" claim is false. Delete route + handler wiring in `main.py`, drop its `_with_route_limit` registration, update `cli_session_end.py`'s module docstring and both CLAUDE.md mentions. `handle_session_end` itself stays (it is the sweep's workhorse). No hook script POSTs to the route (verified across all file types), so no plugin bump.

**D2 — canonicalization.py: delete the dead key codecs, keep the cwd normalizer.** Delete `to_firebase_key` / `from_firebase_key`, their test block, and the contingency pointer in `firebase.py:617-620`'s docstring; rewrite the module docstring to drop the codec story. Keep `canonicalize_cwd` + `CanonicalizationError` + their tests (including the `/mnt/` cases) for in-progress T-026. Annotate T-026: the Option A utility is implemented and tested but display-path wiring was never built — T-026's remaining work is the wiring decision, not the parser.

**D3 — Include a bounded read-only discovery stage.** The review's second pass was never run; the tracked items are only the incidental sightings. The chunk opens with parallel read-only discovery subagents (one per subsystem: server/, tests/, android/, dashboard/, watchtower/, scripts+hooks+skills) hunting dead symbols and near-verbatim duplication. Verification rules are mandatory (see Discovery stage). Confirmed TRIVIAL findings (pure deletions / comment fixes) fold into the fix wave; anything behavior-changing or non-trivial is LOGGED as a new backlog item for John instead of expanding the chunk.

**D4 — Full listener unification per T-225, plus `_now_iso` hoist.** Route the away_mode and status_request listeners through `_start_command_listener` with handler = enqueue-to-queue, keeping the `poll_*` AsyncIterator surface and dispatch loops intact. Hoist `_now_iso` to a single shared def. (The approved T-144 fold turned out moot — see the re-verification ledger: the pattern was already removed by the convening wake rework; T-144 is cut to the ledger as obviated.)

### D4 semantics pins (the unification is only approved with these)

- **Delivery semantics preserved:** handler = queue-put means delete-after-enqueue, which is the bespoke pair's current behavior (delete on enqueue, not delete after command execution).
- **TTL gating is ADDED to away_mode/status_request commands** (stale commands dropped with a phone-visible notice + delete, per `_start_command_listener`'s existing contract). This is a deliberate, desirable behavior change; the dispatch-side staleness handling for `enter_global` stays (double-gating is harmless). Tests asserting stale-command behavior may need the listener-level gate reflected.
- **/healthz listener-name parity:** the supervised listener names visible in `/healthz` must not change (`away_mode_commands`; `status_request` for node `widget/status_request` — note `_start_command_listener` names the supervisor after the NODE, so the status_request registration needs an explicit name override or equivalent to preserve `status_request` as the /healthz key).
- **ABC surface:** `poll_away_mode_commands` / `poll_status_request_commands` / `poll_responses` stay on their ABC(s); only the bespoke listener/enqueue internals collapse.
- If plan grounding finds a hard blocker on any pin, fall back to the minimal variant (parameterize the bespoke pair into one queue-listener helper with zero semantics change) rather than bending a pin.

## Scope

### Wave 1 — verified deletions (server)

Everything in "Confirmed dead — server" above, honoring D1/D2. Includes: the mock/contract-test cleanup that follows each deletion (`test_backend_contracts.py` method lists, `test_firebase_hidden.py` — grounding correction: that file tests `write_conversation_message` auto-unhide, NOT `set_conversation_hidden`; only its dead line-13 `to_firebase_key` import goes, the file stays — `test_firebase_paths.py` blocks, spawn-test mock lines, `RecordingBackend`/`_StubBackend` base lists), and base-class list updates in `gateway/handlers.py:156` / `spawn.py:99`.

### Wave 2 — verified deletions + comment truth (Android)

Everything in "Confirmed dead — Android": delete `adminListener` field, `requestIdToConvId` + false comments, `leftAt` field + read; make `routeConversationMessage` call `isQuestionType`. Fix `submitReplyForConversation` KDoc (T-233 d).

### Wave 3 — DRY consolidation (D4)

Listener unification + `_now_iso` hoist. Also: ConversationViewScreen consumes `row.answeredQuestionMsgIds` (equivalence verified during grounding: splicing reorders but never adds/removes ids, so the derived sets are identical), `locallyAnswered` bounding via a per-conversation suppression pure unit.

### Wave 4 — observability + cosmetics

T-232 malformed-answer logging (thread-bounced, per-iteration binding); `spawn_invalid_path` wiring; T-233 (a) docstring `<conv_id>`, (b) `.env.example` placement, (c) ValueError-branch test in `test_conversation_sweep.py`; CLAUDE.md Layout additions + route-claim edits (D1).

### Discovery stage (D3) — runs first, read-only

Parallel subagents, one per subsystem, each REQUIRED to: verify every candidate with bare-name greps across the whole repo (not just defs — assignment forms too); check producer AND consumer sides together for any cross-surface path (server↔Android↔Operator↔Watchtower); explicitly report negative results. Output: a verified candidate table (symbol, anchor, evidence, proposed action, triviality). The orchestrator folds trivial confirmed items into Waves 1-4 and writes non-trivial findings to a handoff list for John to triage into the backlog. Discovery must not edit anything.

## Out of scope

- WP-7's detail-pane Retry/write-failure UX nicety (feature work, not dead-code/DRY).
- `onNewIntent` (live), `spawn_invalid_path` (wired, not deleted), `update_global_away_cache` (live), `handle_session_end` (live).
- T-026's display-path wiring decision (annotated, stays open).
- Any behavior-changing discovery finding (backlogged instead).

## Verification gates

- `python -m pytest tests/ -q --basetemp="$LOCALAPPDATA/Temp/sb-pytest"` — expect net test-count DROP (deleted-symbol tests) plus adds (T-232 logging, T-233 c, listener-unification, Android units); the plan records the expected baseline delta per task.
- `node --test dashboard/*.test.js` (151 baseline; dashboard likely untouched unless discovery finds something).
- Android: `:shared:testDebugUnitTest` + `:app:compileDebugKotlin` + `:wear:compileDebugKotlin` from `android/` with JDK 21 (never bare `gradlew build`).
- Deletion-invariant greps in the final review: every deleted symbol greps to zero hits repo-wide (all file types); every kept-but-rewired symbol greps to its intended new callers only.
- No service-restart-dependent behavior change except the D4 TTL gating (restart deploys it; no data/schema impact, no plugin bump — hooks/ and SKILL.md untouched by this chunk unless discovery says otherwise, which would trigger the plugin-version-bump rule).

## Tracking updates on land

- CUT backlog sections T-223, T-224, T-225, T-232, T-233, T-144; APPEND completed-ledger rows citing the commit; re-scan intra-doc anchor links after removal (cross-ref hazard from the 2026-07-11 reconciliation).
- Annotate T-026 (per D2) and T-175/T-203 if discovery touches their areas.
- Findings doc: mark the "Incidental dead-code sightings" section and the line-9 second-pass note as executed.
- New backlog items for any non-trivial discovery findings.

## Execution pattern

Fable session (this one) = design tier: this spec, then the plan (`docs/superpowers/plans/`, gitignored), then the SDD kickoff prompt. Execution in a FRESH Opus session per the WP-10 handoff economization (validated; recommended for exactly this chunk). Profile B routing: sonnet implementers explicit on every dispatch, opus task/final reviewers, `CLAUDE_CODE_SUBAGENT_MODEL` unset. Agents never commit — John commits. Snapshot.sh no-commit diffs with pre-staging for CRLF files (dashboard/store.js etc. if touched). Implementer dispatches BAN tree-writing git commands (stash/checkout/restore/reset).
