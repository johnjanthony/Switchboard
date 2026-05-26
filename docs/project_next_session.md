# Next Session Pickup — Page A → Conversation-Keyed Migration

**Branch:** session_id-as-key

**Status:** Plan tasks 1-49 + deferred items 1-7 + T-001 hydration + Phase 1-5 post-review remediation + multi-pass namespace cleanup (`agent_status`, `_admin`, TitleTracker retirement, audit B1/B2/B3, `away_mode` toggle restore) — all landed in the working tree, uncommitted.

**Test suite:** 418 passed, 0 skipped, 0 failures.
**Android build:** clean (`./gradlew :app:assembleDebug` SUCCESSFUL).
**Server boot import:** `from server.main import _run; print('boot OK')` → OK.

## Read these in order

1. **This document** — current state + next session's work.
2. **Memory** — `C:\Users\JohnAnthony\.claude\projects\c--Work-Switchboard\memory\MEMORY.md` and the linked feedback memories (cwd-is-display-only, producer-consumer-audit-pattern, away-mode-control).
3. **Parent design** — [`docs/superpowers/specs/2026-05-19-conversations-collab-redesign-design.md`](superpowers/specs/2026-05-19-conversations-collab-redesign-design.md) — current authoritative state model + tool surface.
4. **T-027 design** — [`docs/superpowers/specs/2026-05-20-spawn-conversation-aware-redesign-design.md`](superpowers/specs/2026-05-20-spawn-conversation-aware-redesign-design.md) — spawn / resume / combine UX.
5. **Tracking** — [`docs/tracking/backlog.md`](tracking/backlog.md) for open items (T-001 partial, T-003, T-029, T-030).

## Next session's primary work: Page A → conversation-keyed migration

### Problem

Android's Page A is still **channel-keyed** (one row per `cwdKey`). The conversations redesign moved the server's primary routing key to `conversation_id`, but the client UI never followed. The mismatch creates:

- **UX wart**: a multi-member conversation across N cwds shows as N separate Page A rows, all carrying the same `ConversationSummary`. They're really one conversation.
- **Bridge maps**: `cwdKeyToConvId`, `msgIdToConvId`, `requestIdToConvId` exist solely to translate Android-UI keys into `conversation_id` for Firebase writes under `/conversations/<conv_id>/...`. Friction at every write site.
- **Legacy channels listener**: still feeds `_channels: Map<String, Channel>` keyed by cwdKey. Reading per-conversation state (hidden, unread, agent_status) requires bridging through the channel structure.

The goal: **make Page A's primary list source `_activeConversations` (keyed by `conv_id`)**, not `_channels`. Each Page A row = one conversation, regardless of how many member cwds.

### Scope

In rough order of risk:

1. **Page A row identity → `conv_id`.** [`SessionListScreen.kt`](android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionListScreen.kt) currently iterates `_channels`; switch to iterate `_activeConversations`. Each row's identity is `conversation.id`, not `cwdKey`. The row displays a member roster instead of a single cwd.

2. **Page B navigation → keyed by `conv_id`.** [`SessionViewScreen.kt`](android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionViewScreen.kt) currently takes a `Channel`; change to take a `ConversationSummary` (and look up its messages by conv_id). Deep links via FCM should resolve to `conv_id` directly.

3. **Per-conversation state migration**:
   - `unread_count` — already migrated to `/conversations/<convId>/unread_count` server-side; Android needs to read from there (currently reads `Channel.unreadCount`).
   - `hidden` — already migrated to `/conversations/<convId>/meta/hidden` server-side; Android reads via `ConversationSummary.hidden` (added in audit-fix pass).
   - `pending_responses` — server still writes to `/channels/<key>/pending_responses` (audit confirmed). Migrate the increment/decrement target to `/conversations/<convId>/pending_responses` AND switch Android to read from there.
   - `agent_status` — already migrated to `/conversations/<convId>/agent_status/<sender>`; `ConversationSummary.agentStatuses` already populated.
   - `last_activity_at`, `title`, `preview` — already on `ConversationSummary` via `/conversations/<convId>/meta`.

4. **Per-cwd legacy channels listener — retire.** Once Page A no longer reads `_channels`, the `setupChannelsListener` and `syncChannel` can either:
   - (a) Stay alive for back-compat / agent_status fallback (the `Channel.agentStatus` fallback in `SessionRowComposable` exists as transitional).
   - (b) Be deleted entirely once `_activeConversations` is the sole source of truth.

   Lean (b) for cleanliness. Decide based on whether any UI surface still needs cwd-keyed data.

5. **Eliminate the bridge maps**:
   - `cwdKeyToConvId` — gone (every call site that used it now has a `conv_id` in hand because rows are conv-keyed).
   - `msgIdToConvId` — gone (the per-conversation message listener already knows its conv_id at routing time; `markMessageOpened` can take `convId` directly).
   - `requestIdToConvId` — possibly still needed for the `/responses/<request_id>` fallback path; or delete if that fallback also retires.

6. **Writers (`hideChannel` / `unhideChannel` / `submitReply` / `selectChannel` / `markMessageOpened` / `clearUnread`)**: all take `conv_id` (or a `ConversationSummary`) instead of `Channel` / `cwdKey`. Each write to `/conversations/<conv_id>/...` directly.

7. **Spawn dialog's "Add to existing" picker**: already reads `_activeConversations` (per Phase 1 fix). No change needed there.

8. **FCM deep-link**: server already emits `conv_id` (per Phase 1 fix #9). The Android handler resolves `conv_id` → navigate to Page B. After migration, this is the natural path; the resolution-through-cwdKey intermediate step can be removed.

### Tests + verification

- Existing tests don't touch Android (no Android unit tests in the repo). Server-side tests should remain green since the server contract is unchanged for this work — it's all Android refactor.
- `./gradlew :app:assembleDebug` must succeed.
- Manual smoke (deferred to actual phone validation): a multi-member conversation across cwds shows as a single row on Page A with member roster; tapping enters Page B; messages from all members appear; replying writes to the right `/conversations/<convId>/answers/<requestId>` path.

### Non-obvious decisions to remember

- **The Channel data class might be retained as an internal projection** — e.g., to bridge per-cwd state (admin notifications, the synthetic `_admin` channel) that's intrinsically not tied to a conversation. Don't blanket-delete `Channel` until you've audited every consumer. The `_admin` synthetic channel (populated by the admin-notifications listener) lives outside the conversations model and may continue to use a cwd-key-shaped identifier.
- **`_admin` is the one path that legitimately stays cwd-keyed** because it's a system-broadcast pseudo-conversation, not a real conversation. Verify it still works after the Page A migration.
- **Don't break the producer/consumer pairs** — the recurring failure mode in this branch. Grep both Android and server simultaneously before deleting any Firebase reader/writer.

### Estimated effort

Half-day to a full day of work in one or two focused dispatches:

- Dispatch A: Page A list source migration + writer migration + bridge-map elimination
- Dispatch B (if needed): per-conversation state migration finish + channels listener retirement
- Decision points to keep open: whether `Channel` data class stays as `_admin` projection.

## Pre-merge validation (your hands required)

Still pending:

1. **Re-run verification scripts** at [`scripts/verify/`](../scripts/verify/) — `test1-session-id.ps1`, `test2-resume.ps1`, `test3-hook-injection/run-test.ps1`. All previously PASSED 2026-05-20. Re-run against current working tree.
2. **Phone-side smoke test**: install APK, exercise spawn (Win + WSL surfaces, create-new + add-to-existing), resume from long-press, combine, force-end, openConversation indicator, ⚠ stale-session warning, reply to ask_human, restart server (verify hydration restores state).
3. **Agent-status sender resolution**: verify that hook events from a running agent show the correct sender name (not "Claude") in Firebase under `/conversations/<convId>/agent_status/<sender>`.
4. **Phone-side away-mode toggle round-trip**: verify pressing the global away pill actually flips the flag (the dispatcher was deleted then restored in this branch; producer/consumer audit confirmed wiring).

After Page A migration, re-validate the above + the new conversation-keyed UI (multi-member rows show correctly).

## Open backlog items (still pending)

- **T-001** (partial): server hydration of conversations + members + routing maps landed. Residual work: pending-request rehydration + "we restarted; resume" broadcast.
- **T-029**: remove `reset_all_away_mode()` after the Stop hook learns to detect MCP unavailability gracefully. Away mode currently resets on every server restart; conversations don't. Asymmetry.
- **T-030**: re-evaluate per-conversation rate-limit parameters. Legacy 60s spawn rate-limit retired in conversations redesign.
- **T-003**: stale-alive member GC (member whose SessionEnd hit a dead server stays alive=True forever).
- **Phase 4 cleanup (test gaps + dead code)** — deferred. Specific items in earlier project_next_session iterations; main remaining cleanups: `bulk_respond.py` (CONFIRMED alive after dispatch restore), `_session_ref` initialized but unused, `/commands` listener infrastructure with no producer, `delete_legacy_away_mode_node` cleanup whose justification expires once Firebase data is uniformly v2.

## Notes for the next agent

- The implementation is in the working tree, NOT committed. Bulk commit gated on manual validation passing.
- Test suite (418/0/0) covers unit + integration paths but does NOT validate the live MCP transport, Firebase path-string consistency across server/Android, or end-to-end semantic round-trip. Manual smoke is required.
- Hydration is wired into `server/main.py:_run`. Conversations survive restart; pending `ask_human` futures don't.
- Away mode resets on startup intentionally (Anthropic #27142 workaround); see T-029.
- Per the producer/consumer audit (2026-05-26): `/channels/<key>/agent_status` MIGRATED to `/conversations/<conv_id>/agent_status/<sender>`. `/channels/_admin/messages` MIGRATED to `/admin_notifications/<push>`. `/channels/<key>/unread_count` MIGRATED to `/conversations/<convId>/unread_count`. `/channels/<key>/hidden` writes are dual-target (legacy + new); reads prefer new. Remaining `/channels/...` writes: `pending_responses` (next migration target), `set_channel_hidden` (renamed to `set_conversation_hidden`, dual-writes during Android migration).
- TitleTracker was retired (was dead code; SKILL.md no longer documents the title-prepend behavior).
