# Goal Description

Stop Switchboard from forcing global away mode back to `false` on service startup, so the away-mode flag survives a service restart (hydrated from Firebase). A restart currently runs `reset_all_away_mode()`, which silently disables away mode; this change removes that reset so away mode persists across restarts.

> **Scope note (2026-07-24):** This spec originally carried a second goal - removing the `set_away_mode` MCP tool so away mode is phone/dashboard-only. That goal has been split out to be revisited separately; this spec is now persistence-only, and `set_away_mode` stays as-is. The Goal 2 review notes are preserved in [Appendix A](#appendix-a--deferred-goal-2-remove-set_away_mode-tool) for the future spec.
>
> This reverses the T-029 deferral (`docs/superpowers/specs/2026-07-12-switchboard-roadmap-v2.md:173`), which kept the startup reset until "Anthropic fixes #27142, or persistence UX pain." #27142 is still open, so the trigger is persistence UX pain (see the triggering incident below), reinforced by the live finding that Claude Code now reconnects its MCP session across a restart (Q0) - which removes the reset's original reason to exist.

## Status

- Reviewed and live-probed 2026-07-24. Q0 (does Claude Code reconnect its MCP session after a restart?) answered: **YES, reconnects.**
- Scope reduced to persistence only. Goal 2 (remove `set_away_mode`) deferred to a separate future spec; notes preserved in Appendix A.
- The earlier "restore restart-while-away guidance / add a `-Force` guard" companion change is **dropped**: with persistence + reconnect, restarting while away is safe, so the guard protects against nothing (decided with John, 2026-07-24).
- **Q2 (preserve the stale-command clear) DECIDED 2026-07-24: Option 1** - narrow `reset_all_away_mode()` to a command-only clear (renamed `clear_pending_away_mode_commands()`, keep the queue wipe, drop only the flag reset) rather than deleting it wholesale.
- **IMPLEMENTED + LIVE-VERIFIED 2026-07-24 (uncommitted).** Code + tests + doc corrections landed on disk; full test suite 982 passed / 1 skipped. Live end-to-end persistence CONFIRMED: away mode toggled ON, service restarted, `/stats` reported `away_mode: true` + `healthy: true` afterward (old code would have shown `false`); the same run re-confirmed Claude Code reconnects its MCP session across a restart. The live smoke harness (`scripts/smoke/smoke.py`) Flow 4 assertion was flipped from `active: false` to `active: true` to match the new contract. Remaining: confirm the reconnect once with a slower / longer-outage restart (observations so far are ~3s restarts). See PROJECT-JOURNAL 2026-07-24.

## Background - triggering incident (2026-07-24)

An Antigravity (agy) agent doing development work on Switchboard itself ran a service restart (`scripts/restart-service.ps1`) while global away mode was ON. Under the current design the restart's startup `reset_all_away_mode()` silently flipped away mode OFF, so the operator - who was away and relying on phone routing - was dropped back to terminal delivery with no notice. (Agy MCP tools auto-reconnect after a restart, so the agent itself recovered; the damage was the silent away-mode clear.) This is the "persistence UX pain" that motivates the change.

## Why the startup reset existed, and why it is now removable

The reset was introduced by commit `789135e` ("away mode re-enforcement. Uninterrupted away mode mcp service restarts!") for one reason: in stateful HTTP mode a restart 404s every live MCP session, and if away mode stayed ON those agents would be trapped in a Stop-hook loop (the turn-end hook says "call `ask_human`", but the tool is dead from the restart). Forcing away mode OFF on startup let those agents fall back to terminal output instead. The cost was that a restart silently disabled away mode - the triggering incident.

The live probe (Q0, below) shows that premise no longer holds: current Claude Code re-establishes its MCP session across a restart on its own. With tools reconnecting, an agent is not trapped - after a restart it reconnects, away mode is still ON, the hook blocks, and `ask_human` works again. So the reset no longer protects against anything; it only causes the silent-clear harm. Removing it is safe.

### What "safe restart" means today vs. after this change

The project history established that restarting while away is "safe." That claim is true but narrow: it means an agent will not be trapped in a Stop-hook loop, achieved by forcing away mode OFF. "Safe for the agent" is not "preserves away mode" - the silent clear is precisely the harm. This change flips which property is sacrificed, and Q0 shows the sacrificed one (agent safety) is no longer at risk:

| | Agent stuck in Stop-hook loop? | Away mode preserved across restart? |
|---|---|---|
| **Today** (startup auto-clear present) | No - the auto-clear frees pre-restart agents to fall back to terminal | No - away mode is silently forced OFF (the triggering incident) |
| **After this change** (auto-clear removed) | No - Claude Code reconnects (Q0), so the agent resumes with tools and away mode still ON | Yes - the flag survives the restart |

### Q0 probe result (2026-07-24): RECONNECTS

Live probe against the deployed service. Pre-restart baseline: `ask_human` reached the phone and returned a reply (tools available, away routing working). The service was then restarted (`restart-service.ps1 -SkipTests`, ~3s down). Post-restart, a fresh `notify_human` from the same, un-relaunched Claude Code session returned a clean server envelope (the at-desk redirect), proving the MCP session re-established itself with no `/exit` + relaunch. This matches the 2026-07-16 observation in `docs/superpowers/specs/2026-07-14-antigravity-cli-support-design.md:114` and contradicts the older #27142 "drops permanently" comment (`server/main.py:416`, `CLAUDE.md`); current Claude Code reconnects. The same call's "John is at his desk" redirect also confirmed the restart auto-cleared away mode - the current behavior this change removes.

**Caveat: n=1, fast `-SkipTests` restart (~3s down). Confirm once with a slower restart / longer down-window before treating "reconnects" as an invariant** (see the Verification Plan).

## User-visible change

After this change, restarting the service no longer turns off away mode. To clear away mode, use the phone/dashboard toggle (or, until Goal 2 lands, the still-present `set_away_mode` MCP tool). A restart is no longer an escape hatch for "away mode stuck on" - but per Q0 that scenario no longer occurs, because a pre-restart agent reconnects rather than getting stuck.

## Proposed Changes

### Server & Persistence

- **Remove the flag reset (the persistence change):** stop forcing `global_settings/away_mode` to `false` on startup. Currently `server/main.py:743` calls `await backend.reset_all_away_mode()` before `load_away_mode_snapshot()` (line 746); the snapshot then reads the just-cleared value. With the flag reset gone, `load_away_mode_snapshot()` hydrates the true persisted value. Update the comment block at `main.py:735-742`, which documents the now-removed rationale.
- **Preserve the stale-command clear (Chesterton's Fence):** `reset_all_away_mode()` (`server/firebase.py:342-366`) does **two** jobs - it forces the flag off AND it clears the `/away_mode_commands` queue so a stale queued toggle left by a crash-before-delete cannot replay when the command listener attaches (decided 2026-06-11, "M06"). The clear must survive this change and must still run **before** `start_away_mode_listeners` (`main.py:749`). Do not delete it along with the flag reset. See Q2 for the shape.
  - **Decided approach (Option 1, 2026-07-24):** repurpose `reset_all_away_mode()` into a command-only clear - remove the `db.reference('global_settings/away_mode').set(False)` line (`firebase.py:359`), keep the `db.reference('away_mode_commands').delete()` line (`firebase.py:365`) - and rename it to reflect the narrowed job (e.g. `clear_pending_away_mode_commands()`). Update the abstract method in `server/messenger.py:175`, the implementation and docstring in `server/firebase.py:342-366`, and the call site in `main.py:743` accordingly. This keeps the current stale-command behavior byte-for-byte while dropping only the flag reset.

### Documentation (correct now-stale claims)

Several docs currently assert that a restart clears away mode; those become false and must be corrected (not merely left).

- **`CLAUDE.md` MCP-transport note (~line 269):** the sentence "Mitigation: server startup auto-clears away mode globally, so pre-restart agents fall back to terminal output rather than getting stuck in a Stop-hook loop" becomes false. Reframe: a restart no longer clears away mode; away mode persists; and Claude Code reconnects its MCP session (observed 2026-07-24), so pre-restart agents resume with tools rather than getting stuck.
- **`CLAUDE.md` "Recovery when the turn-end hook blocks" section and `AGENTS.md:257`:** remove "Restart the service" as a way to force away mode OFF (it no longer does). Keep the phone/dashboard toggle and, until Goal 2, the `set_away_mode` path as the recovery mechanisms.
- **`PROJECT-JOURNAL.md`:** add an entry - away mode now persists across restart; the startup reset was removed; T-029 activated; the Q0 reconnect finding is the enabling evidence.
- **No "do not restart while away" guidance and no `restart-service.ps1` guard.** Considered and dropped: persistence + reconnect make restarting-while-away safe, so there is nothing to guard against.

### Tests

- **Update (reference the changed method):**
  - `tests/test_away_reset_clears_commands.py` - should still pass; repoint it to the renamed command-only clear. This is the regression guard for the behavior being preserved.
  - `tests/test_firebase_paths.py::test_reset_all_away_mode_writes_global_false` - the flag reset is gone; delete this assertion, or replace it with a test that startup does NOT write `away_mode=false`.
  - `tests/test_backend_contracts.py` - update the enumerated backend trait method name if `reset_all_away_mode` is renamed.
- **Add:**
  - A persistence test - away mode ON in Firebase survives startup (`load_away_mode_snapshot` leaves the registry flag `true`; nothing on the startup path resets it). Natural home: `tests/test_hydration.py` or `tests/test_registry.py`.
- No `set_away_mode` test changes here - deferred with Goal 2 (Appendix A).

## Verification Plan

### Automated Tests
- Run `pytest`. Expect: the flag-reset assertion updated/removed, the command-clear test still green (behavior preserved), and the new persistence test green.

### Manual Verification
1. **The fix (re-run the incident):** toggle away mode ON from the phone, restart the service (`restart-service.ps1 -SkipTests`), and verify away mode remains ON (phone pill and `/stats` `away_mode: true`). This directly confirms the triggering incident is fixed.
2. **Q0 follow-up (recommended):** repeat the reconnect probe with a slower restart / longer down-window to confirm current Claude Code still reconnects its MCP session (the existing result is n=1, ~3s). If a longer outage does NOT reconnect, revisit whether the silent-clear was partly load-bearing after all.

## Open Questions & Concerns

- **Q0 - ANSWERED 2026-07-24: reconnects.** See the probe result above. Remaining follow-up folded into manual verification step 2 (confirm with a slower restart before treating as an invariant).
- **Q2 - How to preserve the stale-command clear? DECIDED 2026-07-24: Option 1 (narrow the method, do not delete it).**

  *Background (self-contained).* `reset_all_away_mode()` does two jobs on startup: **Job A** forces `global_settings/away_mode` off (`firebase.py:359`), and **Job B** wipes the `/away_mode_commands` queue (`firebase.py:365`). Goal 1 removes Job A only; Job B must survive.

  *Why Job B matters.* The phone does not flip the flag directly - toggling writes a command (e.g. `{type: "exit_global"}`) into `/away_mode_commands`, which a listener processes (flip flag, resolve pendings) then deletes. If the server crashes after processing but before deleting, the stale entry survives; on restart the listener reads the initial snapshot of that path and re-processes it. A replayed `exit_global` would wrongly turn away mode OFF and bulk-resolve pending questions long after the fact. Job B wipes the queue on startup, before the listener attaches, to prevent that. A weaker backstop also exists: the 10-minute `COMMAND_TTL_SECONDS` gate (`dispatch.py:475`) drops commands older than 10 minutes, so Job B only matters for a stale command younger than that - narrow but real.

  *The two options considered.* Option 1: delete only Job A's line, keep Job B, rename the method to `clear_pending_away_mode_commands()`. Option 2: delete the method entirely and add a small dedicated startup step that only wipes `/away_mode_commands`. Both must run before `start_away_mode_listeners` (`main.py:749`).

  *Decision.* Option 1 - less churn, preserves exact current stale-command behavior. Minor wrinkle noted and accepted: Job B's original rationale ("startup makes an authoritative state decision, so pre-restart toggles are void") weakens once startup no longer decides the flag, but a replayed stale toggle is a bug regardless and the TTL bounds it, so keeping Job B is the minimal-surprise call.

---

## Appendix A - DEFERRED: Goal 2 (remove `set_away_mode` tool)

> Split out of this spec on 2026-07-24 to be revisited separately. Preserved here so the review work is not lost. NOT in scope for the persistence change above. When picked up, promote this into its own dated spec.

**Goal:** delete the `set_away_mode` MCP tool so away mode is toggled exclusively from the phone/dashboard UI.

**User-review points (unresolved):**
- Removal kills the founding verbal workflow: "I'm stepping away" no longer auto-enters away mode and "I'm back" no longer exits it; the operator must toggle from the phone. Is that acceptable, or is the real problem (agents mis-toggling) better fixed by tightening the SKILL guidance while keeping the tool?
- An agent blocked by the turn-end hook could no longer turn away mode off even if told to.

**Removal surface (verified against HEAD 2026-07-24 - the original draft's `GatewayHandlers` protocol and `get_gateway_tools` map do NOT exist):**
- `@mcp.tool() async def set_away_mode(...)` registration in `server/main.py:626-641`.
- The inner `async def set_away_mode(...)` closure in `build_tool_handlers` in `server/gateway/handlers.py:760-808`. It also owns the tool-side pending bulk-resolve; the phone-side `exit_global` path in `dispatch.py` performs the equivalent, so exit-with-resolve is preserved there (verified).
- The `set_away_mode` field on the `ToolHandlers` dataclass (`handlers.py:173`) and its wiring `set_away_mode=set_away_mode` in the `build_tool_handlers` return (`handlers.py:972`).
- No change to `server/spawn.py`: it enables away mode via `backend.set_global_away_mode(True)` directly, not the tool (verified).

**Test blast radius (~15 files, not the 3 in the original draft):**
- Check FIRST: `tests/conftest.py` - if the shared fixture constructs `ToolHandlers(set_away_mode=...)`, removing the field breaks the fixture and cascades.
- Delete (test the removed handler): `tests/test_set_away_mode.py`, `tests/test_set_away_mode_persist_failure.py`, `tests/test_set_away_mode_false_persist_failure.py`.
- Update (assert the tool is registered / in the tool list): `tests/test_mcp_integration.py`, `tests/test_mcp_wrapper_integration.py`, `tests/test_handler_observability.py`, `tests/test_e2e_away_mode_chain.py`.
- Rewrite: `tests/test_away_exit_pending_resolution.py` (verify resolution fires via the phone `exit_global` path instead of the tool); `tests/test_ask_human_away_exit_race.py` (drop the tool-side driver branch, keep the phone-side).
- Assert on reason strings: `tests/test_turn_end_hook.py`, `tests/test_away_guard_hook.py` (the "call `set_away_mode(false)`" lines in `REDIRECT_REASON_AWAY_MODE` / `DENY_REASON` would be edited).
- `scripts/smoke/smoke.py`: drop `set_away_mode` tool assertions; drive away mode via the phone-command path or a direct `/away-mode` POST instead.

**Doc updates (Goal 2):**
- `skills/switchboard/SKILL.md`: strip the "call `set_away_mode(true/false)`" instructions; state away mode is phone/dashboard-only.
- `scripts/turn-end-hook-away-mode.py:49` and `scripts/away-mode-tool-guard-hook.py:39`: remove the `set_away_mode(false)` guidance lines from `REDIRECT_REASON_AWAY_MODE` / `DENY_REASON`.
- `README.md`, `CLAUDE.md`, `AGENTS.md`: remove `set_away_mode` from the tool lists and (in the recovery section) the `set_away_mode(false)` recovery path.
- `docs/switchboard-design-spec-comprehensive.md`: remove `set_away_mode` from the end-to-end reference.

**Open question carried over:** does anything else rely on the tool-side bulk-resolve beyond the confirmed phone `exit_global` path (collab flows, smoke harness)?
