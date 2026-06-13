# Next Session Pickup - P2 implemented (post-review), awaiting commit

**Branch:** session_id-as-key. **Written:** 2026-06-11, updated 2026-06-13 (P1 committed; P2 implemented + independently verified via subagent-driven workflow; P5 plan drafted). Supersedes the prior P1-focused version (history lives in git + [completed-ledger](tracking/completed-ledger.md) + [PROJECT-JOURNAL.md](../PROJECT-JOURNAL.md)).

## Where things stand

**P0, P0-6, T-146, and P1 are committed.** P0 at 37305d2; P0-6 at 938779b; T-146 at 9ecb9ec; the plugin.json version bump at b1f220e; **P1 (away-mode + phone trust) at 95ceeb0**. T-146 is fully closed (code + deployment: plugin.json bumped + committed, `SWITCHBOARD_MARKER_DIR` applied to both hosts via chezmoi). The four 2026-06-11 design decisions are pinned in the [remediation spec](2026-06-11-remediation-spec.md) §9.

**P2 (listener robustness) is implemented and verified this session (2026-06-13), uncommitted, awaiting John's commit.** Executed via subagent-driven-development as a deterministic Workflow (implementer sonnet + spec-review sonnet + code-review opus per task, bounded fix-loops, then a final whole-implementation opus review). Final review returned **ready_for_commit** (0 blocking). Independently re-verified by the controller: full Python suite **483 passed**; Android `:shared:testDebugUnitTest` + `:app:assembleDebug` + `:wear:assembleDebug` **BUILD SUCCESSFUL**. Convention audit (byte-level): all 4 new files CRLF + tab-indented; **zero em-dashes in any added line**. M32 invariant verified intact: the 5 remaining `run_in_executor` calls are all legal on-loop `await`s (firebase.py:489,499; firebase_supervisor.py:160,183,228); every listener callback bounces through `call_soon_threadsafe`.

### P2 acceptance mapping (each spec item -> the passing test/audit)

- **P2-1 (H12/M13)** snapshot replay + TTL + dedupe: `tests/test_firebase_command_listeners.py::{test_combine_command_listener_invokes_handler_on_new_entry, test_force_end_command_listener_invokes_handler_on_new_entry}` (both inverted to assert the initial-snapshot queued command IS dispatched) + `::test_redelivered_command_is_dispatched_once`. Stale-drop with notice: `::test_stale_command_is_dropped_with_notice_not_dispatched` + away belt-and-braces `tests/test_dispatch_away_mode_commands.py::test_stale_away_command_is_dropped_with_notice`. Freshness gate unit-pinned: `tests/test_command_freshness.py` (6 tests incl. fail-open).
- **P2-2 (M32)** no listener-thread loop-affined call: `tests/test_firebase_command_listeners.py::{test_listener_callback_never_calls_run_in_executor, test_schedule_command_delete_bridges_from_a_foreign_thread}` + the `grep -rn run_in_executor server/` audit (5 hits, all legal on-loop awaits).
- **P2-3 (M17/M18)** loud schema-drift degradation: `android .../ParseFailureNoticeTest` (3 cases) + compile-verified MainViewModel wiring (logcat + `_conversationParseFailures` StateFlow + toast; inner member catch degrades to a missing roster entry, not a vanished conversation).

### P2 files touched (uncommitted)

Server: NEW `server/command_freshness.py` (`COMMAND_TTL_SECONDS=600`, `command_age_seconds` fail-open); `server/firebase.py` (3 command listeners collapsed into shared `_start_command_listener` + `_schedule_command_delete`; `_enqueue_away_mode_cmd` bridged); `server/gateway/dispatch.py` (away-command staleness gate).
Android: NEW `android/shared/.../ParseFailureNotice.kt`; `android/shared/.../MainViewModel.kt` (parse-failure collection + toast + StateFlow).
Docs: `docs/2026-06-11-remediation-spec.md` (pointer now P0+P1+P2), `docs/2026-06-11-implementation-plan-p2.md` (Task 3 Step 3 grep prediction corrected from "ZERO hits" to "5 legal on-loop hits remain").
New tests: `tests/test_command_freshness.py`, `android/shared/.../ParseFailureNoticeTest.kt`. Modified tests: `tests/test_firebase_command_listeners.py` (snapshot assertions inverted, stamps refreshed, 4 tests added), `tests/test_dispatch_away_mode_commands.py` (all 8 fixed stamps refreshed to `_now_iso()` so the new TTL gate does not drop the P1-added cancel/blank/skip tests; stale-drop test added).

### Suggested P2 commit message (repo style)

```text
P2: listener robustness (snapshot replay + TTL + thread-safe deletes + loud schema drift)

- P2-1 (H12/M13): collapse the combine/force-end/spawn listeners into one shared _start_command_listener that processes the initial/reconnect snapshot (queued-while-down commands now dispatch), gates dispatch on a 10-minute issued_at TTL (stale commands deleted WITH a phone-visible notice, never executed or silently dropped), and dedupes redeliveries by push-id within a run. New server/command_freshness.py holds the fail-open freshness helper. Away-command dispatcher gains the same belt-and-braces gate.
- P2-2 (M32): every Firebase-listener-thread delete now bounces through call_soon_threadsafe via _schedule_command_delete; _enqueue_away_mode_cmd made uniform. Audit confirms no listener callback calls a loop-affined API directly.
- P2-3 (M17/M18): Android conversation parse failures degrade loudly (logcat + a StateFlow + a toast via the pure conversationParseFailureNotice); a bad member degrades to a missing roster entry instead of vanishing the whole conversation.

Python suite 483 passed; Android shared tests + app/wear builds green. T-029 stays as-is (not removable): a restart still invalidates every CC MCP transport, which stale-command hygiene does not change.
```

## Remaining work, in order

1. **John commits P2**, then (if continuing) says go for P5.
2. **P5 plan is drafted this session** at [2026-06-13-implementation-plan-p5.md](2026-06-13-implementation-plan-p5.md) (12 tasks: observability, away-chain test, title writer, home-pointer preserve change, doc fixes, + the 7-item low-severity shortlist). Its two open decisions are RESOLVED (see below; baked into Tasks 7 and 9). Ready to execute via the same subagent-driven workflow, but ONLY after P2 is committed: P5 edits the same files P2 touches (`server/firebase.py`, `server/gateway/dispatch.py`, `server/gateway/handlers.py`, `MainViewModel.kt`), so running it on uncommitted P2 would make the two commits inseparable. NOT yet implemented.
3. **P4 plan (Wear minimal rebuild)** still needs John's input on Wear screen shapes (R2 settled the away-surface question: minimal read+reply, no away affordance; P4-3 deletes the dead `WearBulkRespondDialog`). P4-1 prerequisite: write Wear regression tests for current behavior before the rewrite (F-89). Needs deep Wear exploration.

## P5 decisions (RESOLVED by John, 2026-06-13; baked into the P5 plan)

- **F-66/F-73 (answered_question_msg_ids): DELETE** the dead write path + correct the `hydration.py` docstring (the phone derives answered-state from message flags; `pending_questions` stays because P1's startup sweep consumes it). P5 Task 9.
- **F-67 (set_away_mode persist failure): YES** - return an ERROR/degraded string when the Firebase persist raises (no longer returns ok on a registry/phone split-brain). P5 Task 7.

## Notes for next agent

- Ground rules (binding): tabs (Python AND Kotlin), CRLF + `unix2dos` for every NEW file, no em-dashes in authored text, predicted-failure TDD, no git writes (John commits). Python: `.venv\Scripts\python.exe -m pytest tests/<file> -v` from repo root. Android: `JAVA_HOME=C:\Program Files\Android\Android Studio\jbr` then `.\gradlew.bat ...` from `android/`; first build after a clean transforms cache can hit a Sophos AccessDeniedException - just re-run.
- The atexit `PermissionError` on the `pytest-current` temp-dir symlink is benign post-session noise.
- Suite count reference: **483 passed** as of P2 (was 472 post-P1, 459 post-T-146).
- P1/P2 plan checkboxes were not ticked during execution; this file + the workflows' per-task review approvals are the completion record.
