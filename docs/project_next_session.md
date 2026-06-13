# Next Session Pickup - remediation P0/P1/P2/P5 committed + holistically reviewed; P4 is next

**Branch:** session_id-as-key. **Written:** 2026-06-11, updated 2026-06-13 (P1+P2+P5 all committed; final holistic cross-phase review done, 0 blocking). The remediation pass (P0, P1, P2, P5) is complete except P4. History lives in git + [completed-ledger](tracking/completed-ledger.md) + [PROJECT-JOURNAL.md](../PROJECT-JOURNAL.md).

## Where things stand

**Committed:** P0 (37305d2), P0-6 (938779b), T-146 (9ecb9ec), plugin.json bump (b1f220e), **P1 (95ceeb0)**, **P2 (501dfcd)**. T-146 fully closed (code + deployment: plugin.json bumped + committed; `SWITCHBOARD_MARKER_DIR` applied to both hosts via chezmoi). The four 2026-06-11 design decisions are pinned in the [remediation spec](2026-06-11-remediation-spec.md) section 9.

**P5 (observability, tests, docs, low-severity cleanup) is implemented, verified, and committed (0e7f80f) this session (2026-06-13).** Executed via subagent-driven-development as a deterministic Workflow (implementer sonnet + spec-review sonnet + code-review opus per task across 11 tasks, bounded fix-loops, then a final whole-implementation opus review). Final review returned **ready_for_commit** (0 blocking). The two open decisions were resolved by John (2026-06-13): **F-67 = return ERROR on persist failure**, **F-66/F-73 = delete the dead `answered_question_msg_ids` write path**; both baked into the plan and implemented.

Independently re-verified by the controller: full Python suite **495 passed**; Android `:shared` tests + `:app`/`:wear` builds **BUILD SUCCESSFUL** (P5 changed no Android code). Convention audit (byte-level): all 10 new test files CRLF + tab-indented; **zero em-dashes in any added line**; diff is 15 modified + 10 new files (130/111 lines), no scope creep, no mass line-ending conversion. **Controller strengthened one test beyond the workflow output:** `tests/test_session_fallback_preserves_home.py` originally asserted only the in-memory home pointer, which survives even the old buggy code (`unbind_session` never touched the home pointer), so it did not actually guard M01/M34. Added `backend.set_session_home.assert_not_awaited()` (the real guard: the unbind arm must not issue the Firebase home delete) and dropped the dead `remove_session_binding` mock. Verified green.

### P5 acceptance mapping (each item -> the passing test/verification)

- **P5-1 observability (M35/M42):** `tests/test_handler_observability.py` (leave/set_away_mode/lookup assert an `event==info` line; open/enter/combine log lines verified by code reading). Round-trip is reconstructable from `switchboard.jsonl`.
- **P5-3 title writer + F-80 (M3):** `tests/test_conversation_title_writer.py`; `write_conversation_meta` converted from `ref.set` to `ref.update` (no clobber).
- **P5-2 away chain (H17/M12):** `tests/test_e2e_away_mode_chain.py` (enter -> registry True -> GET /away-mode True; exit send_default -> False + pending resolved).
- **F-70:** `tests/test_cli_session_end_last_seen_seq.py` (woken member `last_seen_seq == len(conv.messages)`).
- **F-69(g):** `tests/test_spawn_resume_clears_open_pointer.py` (`set_open_conversation_id(None)` awaited on source-ended resume).
- **F-72:** `tests/test_open_conversation_promote_guards.py` (Ended-conv promote rejected; bound-but-not-member guard).
- **F-75:** `tests/test_on_response_lambda_capture.py` (malformed entries log distinct slots) + dead `aclose()` block removed.
- **F-67 (resolved YES):** `tests/test_set_away_mode_persist_failure.py` (persist failure -> ERROR string).
- **P5-4 home-pointer (M01/M34):** `tests/test_session_fallback_preserves_home.py` (strengthened; `remove_session_binding` deleted from firebase.py + messenger.py; `set_session_home(None)` stale-cleanup deleter intact).
- **F-66/F-73 (resolved DELETE):** `tests/test_ask_human_no_answered_write.py` + updated `test_gateway_ask_human.py`; `mark_question_answered` removed (call + backend method + protocol decl); hydration docstring corrected; `pending_questions` writes retained (P1 sweep reads them).
- **P5-4 docs:** comprehensive spec section 10 (answers/away_mode_commands/agent_status) + 12.7 Wear + 13.3, in-repo `CLAUDE.md` env-var unification, F-78/F-83/F-85/F-68/F-71/F-84/F-63/F-62. (F-85: `server/collab.py` confirmed absent.)

### P5 files touched (uncommitted)

Server: `firebase.py` (write_conversation_title + meta set->update; dead aclose block removed; _on_response lambda capture fix; remove_session_binding + mark_question_answered deleted), `gateway/handlers.py` (6 success logs; title wiring; promote guards; set_away_mode ERROR; combine log gated on non-ERROR; mark_question_answered call removed), `messenger.py` (write_conversation_title decl; remove_session_binding + mark_question_answered decls deleted), `cli_session_end.py` (last_seen_seq), `spawn.py` (set_open_conversation_id(None)), `session_fallback.py` (unbind preserves home), `hydration.py` (docstring), `canonicalization.py` (docstring).
Docs: `docs/switchboard-design-spec-comprehensive.md`, `CLAUDE.md` (in-repo), `skills/switchboard/SKILL.md`, `docs/tracking/backlog.md`.
Tests: 10 new (`test_handler_observability`, `test_conversation_title_writer`, `test_cli_session_end_last_seen_seq`, `test_spawn_resume_clears_open_pointer`, `test_open_conversation_promote_guards`, `test_on_response_lambda_capture`, `test_set_away_mode_persist_failure`, `test_session_fallback_preserves_home`, `test_ask_human_no_answered_write`, `test_e2e_away_mode_chain`); modified `test_gateway_ask_human.py`, `test_firebase_writes_for_handlers.py`, `tests/conftest.py`.

### Suggested P5 commit message (repo style)

```text
P5: observability, tests, docs, and low-severity cleanup

- P5-1 (M35/M42): success-path logger.info in the 6 MCP conversation handlers; a combine/open/enter/leave round-trip is now reconstructable from switchboard.jsonl (combine logs only on success).
- P5-3 + F-80 (M3): add write_conversation_title (ref.update) wired where conv.title is mutated post-creation; convert write_conversation_meta from ref.set to ref.update so it can no longer clobber sibling meta fields.
- P5-2 (H17/M12): in-process away-mode chain end-to-end test (phone command -> registry flip -> /away-mode gating -> pending resolution).
- P5-4 home-pointer (M01/M34): the away-off unbind preserves the durable home pointer (no longer deletes the Firebase side); remove_session_binding is deleted; the stale-cleanup set_session_home(None) deleter is intact.
- Low-severity shortlist: F-70 (cli_session_end advances last_seen_seq), F-69(g) (source-ended resume persists the open-pointer clear), F-72 (open_conversation rejects promoting an Ended conversation), F-75 (delete dead aclose block + fix _on_response lambda late-binding), F-67 (set_away_mode returns ERROR on Firebase persist failure), F-66/F-73 (retire the dead answered_question_msg_ids write path).
- P5-4 docs: comprehensive spec section 10 schema (answers / away_mode_commands / agent_status) + 12.7 Wear + 13.3; in-repo CLAUDE.md env-var unification (SWITCHBOARD_BASE_URL for both HTTP hooks, SWITCHBOARD_MARKER_DIR for the marker-file hook); F-78/F-83/F-85/F-68/F-71/F-84 doc-drift; F-63 ageout scope; F-62 FCM wontfix rationale.

Python suite 495 passed; Android shared tests + app/wear builds green.
```

## Holistic cross-phase review (2026-06-13) - DONE

A final whole-remediation review of the integrated P0+P0-6+T-146+P1+P2+P5 (commit range 75e5c6b..0e7f80f) ran via 6 fresh opus reviewers (cross-phase seams + spec-completeness) + a synthesis pass. **Verdict: issues_found but 0 BLOCKING.** All P0/P1/P2/P5 requirements + the four spec section-9 decisions are satisfied and test-backed; section-10 non-goals respected; 495 tests pass. The remediation is sound and complete. The review surfaced findings the per-phase reviews structurally could not (now tracked as backlog items):

- **T-148 (important, cross-phase, VERIFIED at source): request_id-blind pending resolution.** `registry.resolve` / `registry.remove` (registry.py:185,197) key only on `(conversation_id, sender)`; `IncomingResponse` carries request_id only in the cleanup `slot` string, and `dispatch_responses` (dispatch.py:102) matches on (conv,sender). Two manifestations: (a) ANSWER MISROUTE - a listener reconnect in the window between resolving Q1 and the fire-and-forget slot-delete landing can replay Q1's answer onto a newly re-asked Q2 under the same (conv,sender) key, resolving it with stale text (silent; P1's `_on_answer` snapshot-replay widened reachability); (b) SUPERSEDE+CANCEL RACE - the superseded asker's shielded cleanup calls `remove(conv,sender)`, removing the NEW live entry. Fix once: carry request_id on `IncomingResponse` and verify it in `resolve()`/`remove()` against `record.request_id`. Core change (registry + dispatch_responses + `_on_answer`/`_on_response` + supersede cleanup in handlers.py); deserves a deliberate TDD pass reproducing both. The (conv,sender) keying predates the remediation.
- **T-149 (important, pre-existing; reviewer-flagged, NOT yet independently confirmed): handle_resume does not reset last_seen_seq.** combine-resume (`_perform_combine`) sets it to 0; `handle_resume` (spawn.py ~440) does not, so a real dormant member (high last_seen_seq) wakes to EMPTY context (`_compose_wake_payload` slices `messages[last_seen_seq:]`) despite the resume prompt promising recent history. P0-2's acceptance test passes only because its fixture defaults seq=0. Confirm, then mirror the combine-resume reset.
- **T-150 (important, test-fidelity): integrated paths green but unguarded.** (a) the `_on_answer` snapshot-replay test asserts only that `call_soon_threadsafe` fired, not the resolution, so T-148's misroute is invisible to tests; (b) `set_away_mode(False)`+pendings+persist-fail (F-67) has zero coverage because `RecordingBackend` defines neither `set_global_away_mode` nor `set_away_mode`, so the persist try-block is a silent no-op; (c) the away_mode_commands listener has no foreign-thread bounce test and no per-run dedupe set.
- **T-151 (minor cleanup batch):** dead `elif hasattr(backend,"set_away_mode")` fallback (handlers.py:844, spawn.py:234 - no backend defines `set_away_mode`, only `set_global_away_mode`); vestigial `_resp_listener`/`_away_mode_cmd_listener` `__init__` assignments (firebase.py:83,90 - no reader after P5 deleted the aclose block); `resolved`-count over-report in set_away_mode (handlers.py:837); away-cmd listener lacks the per-run dedupe set; stale test mocks of the P5-deleted `remove_session_binding` (test_e2e_spawn_resume / test_firebase_writes_for_handlers / test_spawn_handler / test_gateway_dispatch_combine_force_end) and `mark_question_answered`; canonicalization.py docstring drift (display-only vs "routing key"); pre-existing agent_status stick-holder doc drift (spec lines 204,219).

**Disposition (John, 2026-06-13): fix the safe spec-doc contradictions now; record the code findings (T-148..T-151) for a deliberate pass.** The 3 self-contradicting comprehensive-spec lines were a P5 Task 11 doc miss (it rewrote section 10's schema but left these cross-refs): line 218 (cli-session-end "POSTs" -> writes a marker file swept by dispatch_session_end_markers, per T-146), line 453 (`answered_question_msg_ids` listed as a live subtree -> retired by P5/F-66/F-73; `pending_questions` is server-read by P1's startup sweep, not phone-display), line 487 (`conversation_answers/<conv_id>/<sender>/<push_id>/` -> `conversations/<id>/answers/<request_id>`). These are corrected.

## Remaining work

1. **P5 is committed (0e7f80f); the P0/P1/P2/P5 remediation tracks are all landed and holistically reviewed (0 blocking).** Next: triage the review findings T-148..T-151 (T-148, the request_id race, is the only one with correctness teeth and wants a deliberate TDD pass) and/or proceed to P4 per priority.
2. **P4 (Wear minimal rebuild)** is the only track left, and it needs John's input on **Wear screen shapes**. Settled inputs: R2 (Wear is minimal read+reply, no away affordance), P4-3 deletes the dead `WearBulkRespondDialog` (F-91), P4-2 adds `_admin` sentinel guards (R3), P4-4 retires the legacy `/responses` path (dissolves F-74). P4-1 prerequisite: write Wear regression tests for current behavior before the rewrite (F-89; F-90 deep-link dead-ends noted). Needs deep Wear exploration. P3 (T-141 control-surface) stays deferred until John picks a mechanism.

## Notes for next agent

- Ground rules (binding): tabs (Python AND Kotlin), CRLF + `unix2dos` for every NEW file, no em-dashes in authored text, predicted-failure TDD, no git writes (John commits). Python: `.venv\Scripts\python.exe -m pytest tests/<file> -v` from repo root. Android: `JAVA_HOME=C:\Program Files\Android\Android Studio\jbr` then `.\gradlew.bat ...` from `android/`; first build after a clean transforms cache can hit a Sophos AccessDeniedException - just re-run.
- Pre-existing convention drift noted in P5 review (NOT introduced by P5, candidates for a future whole-file normalization pass): `server/spawn.py` and `server/cli_session_end.py` are whole-file LF (and cli_session_end.py has authored em-dashes in its module docstring). P5 edits matched each file's existing style rather than mixing endings.
- The atexit `PermissionError` on the `pytest-current` temp-dir symlink is benign post-session noise.
- Suite count reference: **495 passed** as of P5 (was 483 post-P2, 472 post-P1).
- Plan checkboxes were not ticked during execution; this file + the workflows' per-task review approvals are the completion record.
