# Next Session Pickup — Conversations Collab + Spawn Redesign

**Branch:** develop (or current feature branch if checked out).
**Status:** Both design specs are complete and approved. Implementation plan is complete. Ready to begin **subagent-driven execution** starting at Task 1.

## Read these in order

1. **The plan** — [`docs/superpowers/plans/2026-05-20-conversations-collab-and-spawn-redesign.md`](superpowers/plans/2026-05-20-conversations-collab-and-spawn-redesign.md). 49 tasks across 9 phases. This is your roadmap.
2. **Parent design** — [`docs/superpowers/specs/2026-05-19-conversations-collab-redesign-design.md`](superpowers/specs/2026-05-19-conversations-collab-redesign-design.md). The current authoritative state model + tool surface. The "Parent design amendments" section of the T-027 spec has been **applied directly** into this doc; the parent IS the source of truth.
3. **T-027 design (paired)** — [`docs/superpowers/specs/2026-05-20-spawn-conversation-aware-redesign-design.md`](superpowers/specs/2026-05-20-spawn-conversation-aware-redesign-design.md). Spawn UX, resume mechanic, combine mechanic, session-file aging warning, server-side flows. Its "Parent design amendments" section is now a slim changelog — refer to the parent for current state.

## Execution mode

**Subagent-driven.** Use the `superpowers:subagent-driven-development` skill. Dispatch a fresh subagent per task with the relevant task content from the plan. Review the subagent's output before moving to the next task. Each task's verification gates the next.

Skill invocation:
```
Skill: superpowers:subagent-driven-development
```

## What's in flight

Nothing. Implementation has not started. Begin at **Task 1: Rewrite `ConversationMember` dataclass** (Phase 1).

## Non-obvious decisions to remember

These came out of long iteration and aren't always obvious from skimming the docs:

1. **Senders are agent-supplied, no uniqueness enforced.** The spawn prompt template encourages distinct names; John can guide via prompt. Server never assigns sender names. Don't add uniqueness checks.

2. **`openConversationId` is a global server-side pointer**, NOT a conversation state. There's no "Open" state. Conversations are just `Active | Ended`, plus the pointer says which Active one is agent-joinable via `enter_conversation()`.

3. **Session-fallback rule on leave / force-end**: members are NEVER orphaned. If away mode is on, the session re-binds to its home conversation (or a fresh one if home Ended); if away mode is off, the session unbinds and uses terminal output. **Combine is exempted** — it moves members to target rather than removing them.

4. **SessionEnd marks members dormant, not auto-leave.** The conversation stays Active. Dormant members are revived via resume or combine. `clear`/`compact` reasons additionally set `session_lost_permanently = True` (un-revivable).

5. **Resume eligibility is "ANY members dormant-and-resumable"** (not all). Partial revival is supported: alive members stay in source, only resumable members fork into the new continuation conversation.

6. **Spawn auto-enables away mode** (`global_away_mode = True`) if currently off. Phone shows a confirmation toast.

7. **`message_and_await_agent` requires non-empty message.** The "listen without speaking" use case is served by `enter_conversation()` (which queues the caller in their current conversation's wait queue when they're already bound, without writing a speak event).

8. **`enter_conversation(sender)` has five branches** — see the parent design's Tool Surface section. Most common: caller already in conv → queue for intro (post-combine / spawn-into-existing case).

9. **Cwd is informational, never a routing key.** `cli_session_id` is the routing key. `canonicalize_cwd` is display-only.

10. **30-day session-file retention** (Claude Code's `cleanupPeriodDays` default). Resume fails visibly when `claude --resume <missing>` errors out — no pre-flight stat. Page A shows a ⚠️ indicator on conversations whose youngest member's `session_ended_at` > 25 days.

## Helper functions referenced ahead of definition

The plan references these helpers in earlier tasks before defining them. Implementer should define them inline as they hit each task; mostly 10–30 line each:

- `_create_active_conversation_for(registry, cli_session_id, cwd, sender)` → Task 15 has the implementation.
- `_inject_combine_intro(registry, target, sender)` → write in Task 19.
- `_wake_one_from(conversation)` → existing FIFO wake mechanism per parent design's "Talking-stick rules"; reuse if present.
- `_migrate_member`, `_add_member`, `_queue_for_intro` → write in Task 17 (`enter_conversation` branching).
- `_format_resume_prompt`, `_format_prompt` → write in Tasks 25/26 (spawn prompt construction).
- `apply_fallback(registry, session_id)` → Task 6 has the implementation.
- `_spawn_pending_for_combine_resume(member, target_id, source_id)` → write in Task 19.
- Test fixtures: `make_active_conversation_with(...)`, `make_conv_with_dormant_members(...)`, `make_conv_with_permanently_lost(...)`, `make_conv_with_mixed(...)` → add to `tests/conftest.py` as factory functions matching the dataclass schemas.

## Verification scripts (already in place)

[`scripts/verify/`](../scripts/verify/) contains reproducible empirical evidence for three key Claude Code behaviors the design depends on. All three PASS as of 2026-05-20. Re-run if Claude Code is updated and you suspect drift.

## Rules of the road (project standards)

- John doesn't want any `git commit` calls. After each task's verification passes, report completion to John; he commits.
- Use tabs for indentation in Java/PowerShell/JSON; Python follows whatever PEP-style the existing files use (look at `server/registry.py` for convention).
- CRLF line endings for files (per global CLAUDE.md).
- Don't update version numbers in build files unless explicitly directed.
- Spec self-review reminders: placeholder scan, type consistency, internal consistency. The plan was self-reviewed at write time; if you find drift while executing, fix inline and continue.
