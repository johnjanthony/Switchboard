# Next Session Pickup - P1 implemented (post-review), awaiting commit

**Branch:** session_id-as-key. **Written:** 2026-06-11, updated 2026-06-13 (P1 implemented + independently verified via subagent-driven workflow; P0 / P0-6 / T-146 now committed). Supersedes the prior P0-focused version (that history lives in git + [completed-ledger](tracking/completed-ledger.md) + [PROJECT-JOURNAL.md](../PROJECT-JOURNAL.md)).

## Where things stand

**P0, P0-6, and T-146 are committed.** P0 remediation (Tasks 1-7) landed at 37305d2; P0-6 (fresh-spawn membership gap) at 938779b; T-146 (reliable SessionEnd dormancy via marker files + server sweep) at 9ecb9ec. The four 2026-06-11 design decisions are pinned in the [remediation spec](2026-06-11-remediation-spec.md) §9. The P0 live smoke is complete (do not re-run). T-146 is now **fully closed** (code + deployment): the plugin.json version bump (1.0.2 -> 1.0.3) is committed, and `SWITCHBOARD_MARKER_DIR` has been applied to both hosts via chezmoi. Both former deployment follow-ups are done; nothing pending there.

**P1 (away-mode + phone trust) is implemented and verified this session (2026-06-13), uncommitted, awaiting John's commit.** Executed via subagent-driven-development as a deterministic Workflow: a fresh implementer (sonnet, strict predicted-failure TDD) + spec-compliance review (sonnet) + code-quality review (opus) per task, with bounded fix-loops, then a final whole-implementation opus review over the complete diff. The final review returned **ready_for_commit** (0 blocking). Independently re-verified by the controller after the run: full Python suite **472 passed**; Android `:shared:testDebugUnitTest` + `:app:assembleDebug` + `:wear:assembleDebug` **BUILD SUCCESSFUL**. Convention audit (controller, byte-level): all 8 new files are CRLF and tab-indented; the only em-dashes on added lines are pre-existing/verbatim-retained (the moved rate-limit error string, consolidated from two inline copies to one helper, and the two SKILL.md tool-bullet separators that already existed at HEAD) - zero newly-authored em-dashes.

### P1 acceptance mapping (each spec item -> the passing test that proves it)

- **P1-1 (H05)** ask_human timeout/error marks question cancelled: `tests/test_ask_human_timeout_cancels_question.py::test_timeout_marks_question_cancelled` (+ updated `tests/test_gateway_ask_human.py::test_ask_human_removes_pending_questions_on_timeout` now asserts `cancelled_questions`). Startup sweep: `tests/test_startup_pending_sweep.py` (cancels every orphan; noop with no conversations).
- **P1-2 (H06)** answers listener replays initial snapshot: `tests/test_firebase_answer_path.py::test_initial_snapshot_replays_undelivered_answers`.
- **P1-3 (H07)** phone badge no longer suppressed; auto-select Wear-only: `android .../SelectionPolicyTest` (5 cases: phone-never-selects, wear-selects-visible-active, wear-does-not-steal, wear-skips-hidden, wear-skips-ended).
- **P1-4 (M14)** staleSessionWarning cannot crash the list: `android .../network/StaleSessionWarningTest` (5 cases incl. server `+00:00` offset in/out of window, zulu, unparseable-degrades-no-throw, null). NB: on the JDK 17 test toolchain `Instant.parse` already accepts `+00:00`, so the unparseable case is the actual M14 crash guard; the offset cases pin window-boundary logic with the real server format.
- **P1-5 (M06)** startup reset clears away_mode_commands: `tests/test_away_reset_clears_commands.py::test_reset_clears_away_mode_commands`.
- **P1-6 (M10)** ask_human rate-limited: `tests/test_ask_human_rate_limit.py::test_at_desk_ask_human_loop_is_throttled` (3rd call throttled, only 2 notifications written). Shared `_rate_limit_error` helper now used by all three tools.
- **P1-7 (R1)** notify_human at-desk sentinel: `tests/test_gateway_notify_human.py::test_notify_human_at_desk_returns_sentinel_and_still_writes` + `test_notify_human_away_still_returns_ok`.
- **P1-8 (M09/M07)** away-exit resolves pendings; phone decision authoritative: `tests/test_away_exit_pending_resolution.py` (resolves-with-notice; plain-when-no-pendings) + `tests/test_dispatch_away_mode_commands.py::{test_exit_global_decision_cancel_does_not_flip, test_exit_global_send_default_blank_text_is_rejected, test_exit_global_decision_skip_flips_but_leaves_pendings}`.

### P1 files touched (uncommitted)

Server: `server/gateway/handlers.py` (timeout/error cancel, notify sentinel, `_rate_limit_error` + ask_human limiter, set_away_mode bulk-resolve), `server/firebase.py` (`sweep_orphaned_pending_questions`, `_on_answer` snapshot replay via `_enqueue_answer`, `reset_all_away_mode` clears commands), `server/main.py` (startup sweep call), `server/gateway/dispatch.py` (exit_global honors `decision`), `server/gateway/bulk_respond.py` (blank send_default validation), `server/hydration.py` (stale-comment fix pointing at the new sweep).
Android: NEW `android/shared/.../SelectionPolicy.kt`, `android/shared/.../MainViewModel.kt` (autoSelect flag + predicate call), `android/wear/.../MainActivity.kt` (Wear opt-in), `android/shared/.../network/Models.kt` (offset-tolerant guarded staleSessionWarning).
Docs/skill: `skills/switchboard/SKILL.md`, `docs/switchboard-design-spec-comprehensive.md`, `docs/2026-06-11-remediation-spec.md` (plan pointer now lists P0 + P1), `docs/2026-06-11-implementation-plan-p1.md` (Task 9 Step 2 premise note corrected).
New tests: `tests/test_ask_human_timeout_cancels_question.py`, `tests/test_startup_pending_sweep.py`, `tests/test_away_reset_clears_commands.py`, `tests/test_ask_human_rate_limit.py`, `tests/test_away_exit_pending_resolution.py`, `android/shared/.../SelectionPolicyTest.kt`, `android/shared/.../network/StaleSessionWarningTest.kt`. Modified tests (collateral, correct): `tests/test_firebase_answer_path.py`, `tests/test_gateway_notify_human.py`, `tests/test_dispatch_away_mode_commands.py`, `tests/test_gateway_ask_human.py`, `tests/test_mcp_wrapper_integration.py`, `tests/test_fresh_spawn_membership.py`, `tests/test_handlers_session_routing.py` (the last two: pre-existing notify_human-in-away-off tests whose `== "ok"` assertions correctly moved to the R1 sentinel).

## Remaining work, in order

1. **John commits P1**, then says go. (Suggested commit message in the session handoff; `docs/project_next_session.md` this update is part of the working tree and can ride the same commit.)
2. **Execute the P2 plan** ([2026-06-11-implementation-plan-p2.md](2026-06-11-implementation-plan-p2.md)) via the same subagent-driven workflow. It asserts P1 landed first: its Task 1/2 anchor against `dispatch_away_mode_commands` as P1's Task 7 left it, and its Task 5 spec-pointer edit builds on P1's Task 10 pointer (now P0 + P1, becomes P0 + P1 + P2). 5 tasks (server + Android). Resolves to: T-029 stays as-is (the startup away-reset is not removable - a restart still invalidates every CC MCP transport; stale-command hygiene does not change that).
3. **Write the P4 plan** (Wear minimal rebuild) while Android context is hot; needs deep Wear exploration plus possibly John's call on screen shapes (R2). The P1-3/P4-1 yank disposition is pinned in the spec. The H07 auto-select-on-arrival quirk for Wear now lives behind the `autoSelectOnMessageArrival` opt-in (Wear opts in); revisit during the rebuild.
4. **Small P5 plan** (observability, title writer, doc fixes): scope pre-chewed by [2026-06-12-low-severity-triage.md](2026-06-12-low-severity-triage.md); home-pointer decision (M01/M34: preserve) recorded in spec P5-4.

## P1 review-flagged follow-ups NOT applied (candidate small items / backlog)

The final whole-implementation review classified these as non-blocking observations and the fix pass deliberately left them alone. None affect correctness or acceptance:

- **Generic-exception cleanup path has no dedicated test** (`server/gateway/handlers.py`): only the timeout path of the mark_question_cancelled change is directly tested; the symmetric error path is uncovered.
- **No behavioral test for the incremental (3-part) `_enqueue_answer` path** (`tests/test_firebase_answer_path.py`): the new test covers only the snapshot branch; pre-existing gap, the plan overstated existing incremental coverage.
- **M14 regression guard is narrow on JDK 17**: only the unparseable case actually guards the crash on this toolchain; an offset-variant unparseable case would harden it.
- **set_away_mode exception asymmetry** (`server/gateway/handlers.py`): if `_apply_bulk_respond_decision` raises, the exception is logged but the flag still flips to False with resolved=0, leaving askers blocked to timeout. Matches the plan's specified code exactly (as-designed); future hardening could keep the flag True on bulk-resolve failure or surface a degraded count.
- **decision=="cancel" guards an unproduced path**: both phone and Wear `onCancel` call `cancelExitToggle` (local dismiss), not `submitExitToggleDecision("cancel", ...)`. The contract test is valid defensive coverage but no shipping caller emits it today.
- **Predicate param naming**: `shouldAutoSelectOnMessageArrival(autoSelectEnabled, ...)` vs the ViewModel property `autoSelectOnMessageArrival`; harmless (positional call site), a single name would reduce the mental hop.
- **Pre-existing LF test files** (`tests/test_firebase_answer_path.py`, `tests/test_mcp_wrapper_integration.py`): LF at HEAD, not a P1 regression (P1's edits are small and additive); worth normalizing to CRLF eventually but out of P1 scope.

## Notes for next agent

- Ground rules (binding through P2 as well): tabs (Python AND Kotlin), CRLF + `unix2dos` for every NEW file, no em-dashes in authored text, predicted-failure TDD, no git writes (John commits). Python: `.venv\Scripts\python.exe -m pytest tests/<file> -v` from repo root. Android: set `JAVA_HOME=C:\Program Files\Android\Android Studio\jbr` then `.\gradlew.bat ...` from `android/`; the first build after a clean transforms cache can hit a Sophos AccessDeniedException - just re-run.
- The atexit `PermissionError` on the `pytest-current` temp-dir symlink is benign post-session noise, not a failure.
- Suite count reference: **472 passed** as of P1 (was 459 post-T-146, 441 pre-P1).
- The plan checkboxes were not ticked during execution; this file + the workflow's per-task review approvals are the completion record.
