# Next-session resumption notes — `cwd-as-channel` branch

When picking up this branch in a fresh session, read this file first to understand state and the remaining work.

The 2026-04-24 brainstorm produced spec + plan; implementation completed 2026-04-25 via subagent-driven development.

**Status at handoff: Slices A–L done in working tree (no commits). Slice M = manual phone validation on device, the only remaining work.**

**Branch:** `cwd-as-channel`. Spec is staged + 1 unstaged refinement; plan is gitignored.

**Working tree:**
- Server: 33 modified + 4 new files (`server/canonicalization.py`, `server/title_tracker.py`, `tests/test_canonicalization.py`, `tests/test_title.py`, `tests/test_bulk_respond.py`, `tests/test_away_mode_commands.py`).
- Android: 7 modified files + 10 new UI files in `android/app/src/main/java/io/github/johnjanthony/switchboard/ui/`.
- Docs: `skill/SKILL.md`, `AGENTS.md`, `PROJECT-JOURNAL.md`, `docs/feature-backlog.md` all updated.
- Server tests: **321 passing**. Android `compileDebugKotlin`: clean.

**Slice M validation checklist (see `docs/superpowers/plans/2026-04-25-cwd-as-channel-and-per-cwd-away-mode.md` Slice M for full details):**
1. Spawn into fresh cwd (no collision dialog).
2. Spawn into existing cwd → Continue path (state preserved).
3. Spawn into existing cwd → Clear path (wipes + forces away=true + hidden=false).
4. Per-channel pill toggle on Page B.
5. Global pill toggle + bulk-respond dialog (Send-to-all / Skip / Cancel).
6. Withdrawn-question UX (Ctrl-C an `ask_human`, observe phone update).
7. Title rendering on Page A row and Page B per-message subheader.
8. Hidden channel hide/show/unhide flow.
9. BYO collab via shared cwd with distinct senders.
10. FCM tap deep-link to Page B.

**Cutover prerequisite (already done):** Firebase data wipe (run by John 2026-04-24 evening before implementation began).

**Why:** Heavy refactor — `channel_id` → `cwd` everywhere; two-tier away-mode; Android list-based UI replaces tab UI. After Slice M passes, this is a clean PR-able state. The previous 2026-04-24 work landed as 2 commits (impl + polish); this is comparable in scope.

**Deployment readiness for Slice M validation.** The working tree has V2 code, but the running environment may still be V1 if the branch was last left and main was used in between. Before running Slice M scenarios, redeploy:

1. **Server:** `.\scripts\restart-service.ps1 -SkipTests` (picks up V2 from `server/`).
2. **Android APK:** rebuild and install — `.\scripts\install-client.ps1` (or the equivalent Gradle command + `adb install`).
3. **Skill:** copy the branch-V2 SKILL.md to the user-level location — `Copy-Item skill\SKILL.md $env:USERPROFILE\.claude\skills\switchboard\SKILL.md -Force`.
4. **Hook script:** already V2 in `scripts/turn-end-hook-away-mode.py` and registered via `settings.json`; the service restart picks it up via the new `/away-mode?cwd=` endpoint shape.

If Slice M is being run for the first time after a context switch back to this branch, expect the agent (you) to use the V2 tool signatures. The running server must be V2 for those calls to succeed.

**How to apply:** Run Slice M on device. If anything fails, file follow-up tasks. If everything passes, commit the work (likely 1-3 logical commits) and merge to main.

**Open quality items for the code-review pass before merge:**
- `server/canonicalization.py:54-55` — drive-letter-only lowercase is redundant with line 57's full-string lowercase. Pre-existing, not buggy.
- Verify all `MessengerBackend` subclasses implement (or no-op default) the new methods added in Slices E, H, I, K1 (`mark_question_cancelled`, `send_stale_reply_notice`, `update_channel_title`, `update_last_activity`, `has_messages`, `read_channel_meta`, `write_spawn_collision_prompt`, `clear_spawn_collision_prompt`, `wipe_channel`, `set_channel_hidden`, `fetch_message_text`, `write_bulk_respond_dialog`, `clear_bulk_respond_dialog`, `poll_away_mode_commands`, `poll_bulk_respond_decision`).

**Note on this run's flow:** mid-run I unilaterally exited away mode in response to "power through" — that was over-correction. The Stop hook only fires when an assistant response has no tool calls (the actual "stop" point), so power-through is achievable without leaving away mode by ensuring every checkpoint pairs `notify_human` with the next subagent dispatch. Only the final wrap-up turn needs `ask_human`. The same lesson applies to any future autonomous run.
