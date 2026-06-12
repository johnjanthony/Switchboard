# Next Session Pickup — P0 remediation (post-review)

**Branch:** session_id-as-key. **Written:** 2026-06-11, replacing the stale 2026-05-26 version (which described Fix Packs 1-9; that history lives in git and PROJECT-JOURNAL.md).

## Where things stand

The 2026-06-11 review produced four docs (all uncommitted): [implementation-review-raw-findings](2026-06-11-implementation-review-raw-findings.md), [verification-verdicts](2026-06-11-verification-verdicts.md), [remediation-spec](2026-06-11-remediation-spec.md), [control-surface-hardening (T-141)](2026-06-11-control-surface-hardening.md).

**All four open design decisions are resolved with John (spec §9):** P0-2 = option (a) extended (hydration binds alive only; member state is the single source of truth for resumability); snapshot policy = process + TTL gate (10 min, notify-on-drop, startup clears away commands + sweeps orphaned pending_questions, at-least-once with per-run dedupe); P1-8 = resolve-with-sentinel unified on `_apply_bulk_respond_decision`; T-141 = **deferred** (no mechanism; excluded from the plan; pickup notes in that doc's §3).

**P0 is implemented.** The [P0 plan](2026-06-11-implementation-plan-p0.md) Tasks 1-7 are done via subagent-driven development, each with TDD red/green evidence and two-stage review (spec compliance + code quality), all APPROVED. Task 8 is partially done: full suite verified green (441 passed, 1 pre-existing warning), spec pointer updated. Everything is in the working tree, NOT committed (John commits).

## What landed (P0 fixes, all in `server/` + tests)

1. **P0-2a/b (Tasks 1-2):** hydration binds alive members only (hydration.py step 4); resume eligibility keys off member state with a `resume_eligibility_drift` loud log (spawn.py); orphan-home regression test; post-restart resume acceptance test (new tests/test_resume_after_restart.py).
2. **P0-4a/b (Tasks 3-4):** module-level `user_has_interactive_session()` + `invoke_spawn_launcher(logger)` in spawn.py (methods delegate); `handle_resume` now gates on a desktop session before any state change; autouse gate fixture added to tests/test_spawn_handler.py (kills env-coupling the new gate would have activated).
3. **P0-1 (Task 5):** `_perform_combine` gates dormant moves on a desktop session, writes the pending file then binds + flips alive together (invariant: bound = alive or relaunch-in-flight), and fires the launcher once via `invoke_spawn_launcher`; `dispatch_combine_commands` gains `pending_dir` and main.py threads `_Path(config.log_path).parent` (H14); new tests/test_combine_resume_launch.py covers gate, bind+alive+launcher, MCP path, phone path.
4. **P0-5 (Task 6):** combine's alive-move now resolves `target.open_peer_future` (mirrors `_migrate_member`); test in tests/test_e2e_combine.py.
5. **P0-3 (Task 7):** `handle_force_end` cancels pending ask_human futures (`cancel_pending_for_conversation`) and marks question records cancelled (`mark_question_cancelled`, which also clears pending_questions records); badge decrements via the pending mirror; optional `logger` param; new tests/test_force_end_cancels_pending.py.

Updated existing tests to the new contract: tests/test_hydration.py (alive-only binding), tests/test_e2e_combine.py + tests/test_combine_conversations.py (dormant member flips alive at combine; launcher asserted).

## Remaining before merge

1. **Final whole-implementation code review** (the subagent-driven skill's last gate): one reviewer over the full P0 diff. Not yet dispatched; first action next session.
2. **John commits** the P0 work (suggest logical commits per task or one P0 commit; his call). Doc changes (spec/T-141/backlog/plan/this file) can ride along.
3. **Live smoke** of the P0 acceptance scenarios from the phone (combine with dormant member actually launches a wt tab; resume after `nssm restart switchboard`; force-end while an ask_human blocks) since none of combine/force-end has ever run live (H18).
4. Then the **P1 plan** (away-mode + phone trust) per spec §11 ordering, same writing-plans + subagent flow.

## Session-surfaced follow-ups (already in backlog.md)

- **T-142** (low): session-end races the spawn/combine desktop-session gate (lock-free `handle_session_end` TOCTOU; parity with handle_resume; fix direction noted).
- **T-143** (low): dormant-only combine does not wake a lobby-holding target opener (self-heals after one timeout; needs a payload-semantics decision).
- **T-144** (low): join-wake pattern now has three identical copies (rule of three met; extract `_wake_open_peer` next touch).
- Pre-existing doc nit found in review: tests/test_spawn_login_check.py module docstring is accurate again now that test_spawn_handler.py really has the autouse fixture; no action needed.

## Notes for next agent

- Read [the P0 plan](2026-06-11-implementation-plan-p0.md) ground rules before touching anything: tabs, no git writes (John commits), `python -m pytest tests/<file> -v` from repo root, no em-dashes in generated text.
- The plan's checkboxes were not ticked during execution; treat this file + the review approvals in the session transcript as the completion record, or tick them on first read.
- Every pytest run on this box prints an atexit `PermissionError` from pytest's temp-dir symlink cleanup (`pytest-current`); it is post-session noise, not a failure.
- Suite count reference: 441 passed as of this writing (was 423 in the previous pickup note, 433 pre-P0).
