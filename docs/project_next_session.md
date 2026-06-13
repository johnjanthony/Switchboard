# Next Session Pickup — P0 remediation (post-review)

**Branch:** session_id-as-key. **Written:** 2026-06-11, updated 2026-06-12 (post P0 commit 37305d2; updated again 2026-06-12 after the live smoke + P0-6 fix). Replaces the stale 2026-05-26 version (Fix Packs 1-9; that history lives in git and PROJECT-JOURNAL.md). **Uncommitted right now:** the P0-6 fix (5 server files + tests/test_fresh_spawn_membership.py), its spec + plan, backlog additions T-145/T-146/T-147, the P2 plan, the low-severity triage doc, the spec's home-pointer decision pin, and this file's updates.

## Where things stand

The 2026-06-11 review produced four docs (committed with the P0 work): [implementation-review-raw-findings](2026-06-11-implementation-review-raw-findings.md), [verification-verdicts](2026-06-11-verification-verdicts.md), [remediation-spec](2026-06-11-remediation-spec.md), [control-surface-hardening (T-141)](2026-06-11-control-surface-hardening.md).

**All four open design decisions are resolved with John (spec §9):** P0-2 = option (a) extended (hydration binds alive only; member state is the single source of truth for resumability); snapshot policy = process + TTL gate (10 min, notify-on-drop, startup clears away commands + sweeps orphaned pending_questions, at-least-once with per-run dedupe); P1-8 = resolve-with-sentinel unified on `_apply_bulk_respond_decision`; T-141 = **deferred** (no mechanism; excluded from the plan; pickup notes in that doc's §3).

**P0 is implemented, reviewed, and committed (2026-06-12).** The [P0 plan](2026-06-11-implementation-plan-p0.md) Tasks 1-7 landed via subagent-driven development with two-stage reviews per task; the final whole-implementation review returned READY FOR COMMIT (suite 441 passed; its two minor finds, LF endings on the three new test files and one stale comment, were fixed pre-commit); John committed.

**P1 and P2 plans are written and ready to execute:** [P1 (away-mode + phone trust)](2026-06-11-implementation-plan-p1.md) (10 tasks, server + Android), [P2 (listener robustness)](2026-06-11-implementation-plan-p2.md) (5 tasks; prerequisite: P1 landed). Both follow the P0 plan's conventions (TDD with predicted failures, no commits, tabs, CRLF, subagent-driven execution recommended).

## What landed (P0 fixes, all in `server/` + tests)

1. **P0-2a/b (Tasks 1-2):** hydration binds alive members only (hydration.py step 4); resume eligibility keys off member state with a `resume_eligibility_drift` loud log (spawn.py); orphan-home regression test; post-restart resume acceptance test (new tests/test_resume_after_restart.py).
2. **P0-4a/b (Tasks 3-4):** module-level `user_has_interactive_session()` + `invoke_spawn_launcher(logger)` in spawn.py (methods delegate); `handle_resume` now gates on a desktop session before any state change; autouse gate fixture added to tests/test_spawn_handler.py (kills env-coupling the new gate would have activated).
3. **P0-1 (Task 5):** `_perform_combine` gates dormant moves on a desktop session, writes the pending file then binds + flips alive together (invariant: bound = alive or relaunch-in-flight), and fires the launcher once via `invoke_spawn_launcher`; `dispatch_combine_commands` gains `pending_dir` and main.py threads `_Path(config.log_path).parent` (H14); new tests/test_combine_resume_launch.py covers gate, bind+alive+launcher, MCP path, phone path.
4. **P0-5 (Task 6):** combine's alive-move now resolves `target.open_peer_future` (mirrors `_migrate_member`); test in tests/test_e2e_combine.py.
5. **P0-3 (Task 7):** `handle_force_end` cancels pending ask_human futures (`cancel_pending_for_conversation`) and marks question records cancelled (`mark_question_cancelled`, which also clears pending_questions records); badge decrements via the pending mirror; optional `logger` param; new tests/test_force_end_cancels_pending.py.

Updated existing tests to the new contract: tests/test_hydration.py (alive-only binding), tests/test_e2e_combine.py + tests/test_combine_conversations.py (dormant member flips alive at combine; launcher asserted).

## P0-6 (fresh-spawn membership gap): discovered + fixed this session, uncommitted

The P0 live smoke (2026-06-12) discovered a sixth P0: `handle_fresh` binds a session and mints a conversation but never creates a `ConversationMember`, so `cli_session_end` found no member and silently no-op'd. The member never went dormant, making phone Resume and combine-relaunch unreachable for the primary spawn flow. Designed ([spec](superpowers/specs/2026-06-12-fresh-spawn-membership-gap-design.md)), planned ([plan](2026-06-12-implementation-plan-p0-6.md)), implemented via subagent-driven development (7 tasks; implementer sonnet + spec-review sonnet + code-review opus each), final whole-implementation review returned READY FOR COMMIT, suite 452 passed. Fix: a shared `_resolve_conversation_and_member` (mint-if-unbound / return-id-unchanged-if-conv-missing / ensure-member-if-bound) wired into all six conversation-participating tool paths (ask_human, notify_human, send_document_human, message_and_await_agent with mint_if_unbound=False, open_conversation, enter_conversation bound-current); loud `surface_error` logs on `handle_session_end`'s three early returns with the logger threaded through main.py; a reinforcing sentence in the fresh-spawn prompt; new tests/test_fresh_spawn_membership.py (11 tests). **Validated live** (fresh spawn -> first tool call creates the member -> manual session-end marked it dormant -> phone showed dormant + Resume). **Uncommitted, awaiting John's commit.** Files: server/conversation_ops.py, server/gateway/handlers.py, server/cli_session_end.py, server/main.py, server/spawn.py, tests/test_fresh_spawn_membership.py.

## Remaining work, in order

1. **John's live smoke (DONE 2026-06-12).** P0-3 force-end frees a blocked ask_human (Scenario A, PASS; T-145 logged for the agent retrying on cancellation). P0-6 membership -> dormancy validated live. P0-2 resume-after-restart fired the launcher and opened a `claude --resume` tab live (Scenario C, PASS). P0-1 combine launcher was not run live but uses the identical `invoke_spawn_launcher` proven by P0-2. Two infra/design blockers surfaced and logged: **T-146** (SessionEnd hook does not reliably POST `/cli-session/end` on a clean `/exit`, so dormancy is unreliable in practice; HIGH) and **T-147** (resume prompt tells single-agent sessions to call enter_conversation, hanging them). Resume mints a `continued_from` continuation conversation by design (does not rejoin the original); John flagged the expectation mismatch, revisit if undesired.
2. **Execute the P1 plan** ([2026-06-11-implementation-plan-p1.md](2026-06-11-implementation-plan-p1.md)) via subagent-driven development; John commits when green. (Consider whether T-146, which blocks real-world dormancy, should be addressed before or alongside P1.)
3. **Execute the P2 plan** ([2026-06-11-implementation-plan-p2.md](2026-06-11-implementation-plan-p2.md)); it asserts P1 landed before touching the away dispatcher.
4. **Write the P4 plan** (Wear minimal rebuild) in a session right after P1 execution while the Android context is hot; needs deep Wear exploration plus possibly John's call on screen shapes (R2). The P1-3/P4-1 yank disposition is pinned in the spec. Then the small **P5 plan** (observability, title writer, doc fixes): its scope is pre-chewed by [2026-06-12-low-severity-triage.md](2026-06-12-low-severity-triage.md) (all 30 LOW findings verified; 7 new small fixes shortlisted; the rest mapped to plans/decisions or doc passes), and the home-pointer decision (M01/M34: preserve) is already recorded in spec P5-4.

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
