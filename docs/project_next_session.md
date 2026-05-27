# Next Session Pickup — Pre-merge validation

**Branch:** session_id-as-key

**Status:** Plan tasks 1-49 + Page A conversation-keyed migration (Dispatches A/B/C) + post-migration bug fixes (Fix Packs 1/2/3) + force-end dormant-member fallback (Fix Pack 4) + combine source.wait_queue migration (Fix Pack 5) + agent-status hook cwd retirement + bulk_respond dead-param cleanup (Fix Pack 6) + dead per-conversation at-desk chain cleanup (Fix Pack 7) + SKILL.md frontmatter gate on away-mode, T-023 closed (Fix Pack 8) + live phone-smoke fixes (Fix Pack 9: float `last_activity_at`, answers-listener thread bounce, hook stdin UTF-8 bytes, plugin 1.0.0 → 1.0.2) — all 2026-05-26, all landed in the working tree, uncommitted.

**Test suite:** 423 passed, 0 skipped, 0 failures.
**Android builds:** `:app:assembleDebug` + `:wear:assembleDebug` both BUILD SUCCESSFUL.
**Server boot import:** `from server.main import _run; print('boot OK')` → OK.

## What landed since the last context compact

### Page A → conversation-keyed migration (Dispatches A/B/C)

- **A** — Server `pending_responses` counter rerouted to `/conversations/<conv_id>/pending_responses`. ConversationSummary on Android gained `pendingResponses`, `preview`, `hidden`, `unreadCount` fields actually populated.
- **B** — Phone Page A now driven by `_conversationRows` (keyed by `conv_id`). Bridge maps `cwdKeyToConvId` + `msgIdToConvId` deleted; `requestIdToConvId` reduced to the Wear-compat legacy `/responses` fallback. Route is `session/{convId}`. Wear keeps building via a derived `_channels` projection. `_admin` rendered as dedicated `AdminRow`.
- **C** — Deleted orphan server methods `write_away_mode_mirror` and `read_channel_meta` (and 9 dependent tests); simplified `reset_all_away_mode` to a single global-flag write.

### Post-migration bug fixes (Fix Packs 1/2/3 — 10 verified bugs)

**Fix Pack 1 (collab protocol):**
1. **`ask_human` at-desk redirect implemented** ([handlers.py:147-234](server/gateway/handlers.py)). When `global_away_mode` is False, the question is written as a `notify` (not `question`) and the handler returns the exact SKILL-documented sentinel `"ERROR: John is at his desk. Ask this question via the terminal."` — no 24h block.
2. **`__CONVERSATION_EMPTY__` now applies session-fallback** in `message_and_await_agent`. Mirrors `leave_conversation`'s removal + end + fallback pattern.
3. **`cli_session_end` wakes blocked peers** AND cancels their pending `ask_human` futures. Blocked peers resolve with the dormancy_msg text rather than blocking 24h.
4. **Hook env-var unified to `SWITCHBOARD_BASE_URL`** across all three hooks (`cli-session-end`, `agent-status`, `turn-end-away-mode`). Per-host chezmoi templating only needs the one variable.

**Fix Pack 2 (persistence/restart-survival):**

5. **`set_conversation_state` now writes `conversations/<id>/meta/state`** (was the orphan top-level `state` path). Ended conversations stay Ended across restart.
6. **Resume sets `m.alive = True`** and clears `session_ended_at` / `session_end_reason` / `left_at` on resumed members. Multi-member resume no longer immediately fires `__CONVERSATION_EMPTY__`.
7. **`members_history` persisted to Firebase** at `/conversations/<conv_id>/members_history/<sender>`. New backend method + abstract trait + hydration restores the array on startup.
8. **Spec-required subtrees written:** `/conversations/<id>/pending_questions/<request_id>/` populated on `ask_human` + drained on resolve/cancel/timeout; `/conversations/<id>/answered_question_msg_ids/<msg_id>/` written when a reply lands. (PendingRequest map itself is intentionally in-memory; restart-orphan cleanup flagged for future work in hydration docstring.)

**Fix Pack 3 (hygiene):**

9. **Wear FCM deep-link resolves conv_id → cwdKey via projection** before navigating. Same `LaunchedEffect` pattern as phone. Notification taps now land in the right conversation through Wear's legacy screens.
10. **Dead Firebase paths deleted** (~135 lines): `db.reference('commands')` + `_on_command` + `poll_commands`; `db.reference('sessions')`; `start_inject_listener` + `_on_inject` + `_inject_queue_internal` + `poll_inject_messages`. The `InjectPort` trait and `poll_commands` abstract method retired alongside.

**Fix Pack 4 (force-end fallback gap):**

11. **`handle_force_end` now applies session-fallback for every member, not just alive members.** Dropped the `if m.alive` filter in [dispatch.py](server/gateway/dispatch.py). `apply_fallback` in [session_fallback.py](server/session_fallback.py) now detects dormant sessions (`session_id not in registry.session_to_conversation_id`) and short-circuits to home-pointer cleanup — clears `session_home_conversation_id[session_id]` if the home conv is gone or Ended, without going through `compute_fallback`'s create-new branch. Prevents both stale-home leaks AND orphan-conv creation for dead sessions. Backend method `set_session_home` extended to accept `None` (deletes the Firebase node). Hydration adds a defensive skip: home pointers referencing a non-hydrated conv are dropped instead of seeded. 5 new tests cover the alive/dormant/no-home/active-home matrix.

**Fix Pack 5 (combine wait_queue migration):**

12. **`_perform_combine` migrates `source.wait_queue` entries to `target.wait_queue`** before ending source ([conversation_ops.py:397-489](server/conversation_ops.py#L397-L489)). For each entry whose member moved to target, the wait_entry is appended to `target.wait_queue` (same member ref, valid future). For entries whose member stayed in source (permanently_lost), the future is drained with sentinel `"__CONVERSATION_ENDED__\n(merged into target)"`. Without this, agents blocked in `message_and_await_agent` on source at combine time would have their futures stranded for the full 24h `_TIMEOUT`. 2 new tests in [test_e2e_combine.py](tests/test_e2e_combine.py) cover the source-waiter-migration and permanently_lost-drain cases. The existing `_wake_one_from(target)` at line 488 stays — migrated entries with older `block_position` get woken first if FIFO ordering applies.

**Fix Pack 6 (hook + dead-param hygiene):**

13. **Agent-status hook no longer transports `cwd`.** [scripts/agent-status-hook.py](scripts/agent-status-hook.py) now gates on `session_id` (not `cwd`) and posts `{session_id, state, detail?}`. [server/main.py](server/main.py)'s `_build_agent_status_route` validates `session_id` instead of `cwd`. [handlers.py:774](server/gateway/handlers.py#L774) `handle_agent_status` signature is now `(session_id, state, detail)` — `cwd` parameter removed entirely. Internal resolution was already session-id-driven; the cwd parameter was pure baggage. Last per-cwd transport key on the hook surface is gone. Existing tests in `test_agent_status_hook.py` + `test_agent_status_integration.py` updated to send `session_id`.
14. **`scope_cwd` dead parameter removed from `_apply_bulk_respond_decision`** ([bulk_respond.py](server/gateway/bulk_respond.py)). The unreachable `else registry.pending_for_conversation(scope_cwd)` branch (caller always passed `None`) is gone — function unconditionally walks `registry.all_pending()`. Docstring + two error-message interpolations updated. Single call site at [dispatch.py:291-298](server/gateway/dispatch.py#L291-L298) updated.

**Fix Pack 7 (dead per-conversation at-desk chain):**

15. **Retired the entire `onExitAway` / `requestSwipeAtDeskForConversation` chain.** Vestige from the retired per-cwd away mode (away is global-only now). The chain had no triggering UI gesture — `SessionRowComposable` declared `onExitAway: () -> Unit` but never called it inside the function body. ~40 lines removed across [MainActivity.kt](android/app/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt) (collectAsState wire-up, onAwayToggle argument, the "Set channel to At desk?" `AlertDialog` block, two newly-unused imports), [SessionListScreen.kt](android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionListScreen.kt) (`onAwayToggle` parameter + its row argument), [SessionRowComposable.kt](android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionRowComposable.kt) (`onExitAway` parameter), [MainViewModel.kt](android/shared/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt) (`_pendingSwipeAtDeskConfirm` + `pendingSwipeAtDeskConfirm` flows, plus `requestSwipeAtDeskForConversation` / `confirmSwipeAtDesk` / `cancelSwipeAtDesk` methods). Tests unchanged (none exercised the dead chain). Historical record preserved in [`PROJECT-JOURNAL.md`](../PROJECT-JOURNAL.md).

**Fix Pack 8 (skill frontmatter gate — closes T-023):**

16. **SKILL.md frontmatter description rewritten to gate `ask_human` / `notify_human` / `send_document_human` on away mode.** The old description's "Invoke ask_human whenever a decision would otherwise stall the task…" sentence read as unconditional invocation guidance, which T-023 documented as the root cause of unwanted sub-agent escalations during active in-channel conversations. New frontmatter bakes the away-mode gate into every trigger sentence, carves out sub-agents explicitly ("Task-tool return to the controller, not via switchboard"), and frames the server's at-desk redirect as a safety net rather than the primary path. T-023 marked `status: closed` in backlog.md with closure note. Body of SKILL.md (the "CRITICAL: Away Mode Protocol" section) was already correctly gated; no body changes needed.

**Fix Pack 9 (live phone-smoke fixes — caught during Task 46 validation):**

17. **`meta/last_activity_at` type-mismatch fixed in [firebase.py](server/firebase.py) `write_conversation_message`.** Spawn wrote it as a float (`conv.created_at`); `write_conversation_message` was overwriting it with an ISO string (the same `now` variable used for the message's `timestamp` field). Android's `startConversationListener` reads it as `Double`, which threw `DatabaseException` on the string, the outer try/catch in the listener swallowed the exception, and the conv got `mapNotNull`-filtered out of `_conversationRows` — Page A rows vanished on the first xxx_human call while the conv stayed `meta/state: active` in Firebase. Fix: write a separate float `now_ts` for the meta update; the message's own `timestamp` field still uses the ISO string.
18. **Answers listener thread-bounce fixed in [firebase.py](server/firebase.py) `_on_answer`.** The listener callback runs on the Firebase SDK's listener thread (no event loop). It called `_spawn_bg(self._response_queue.put(...), ...)` directly, which calls `asyncio.create_task` and requires a running loop — raising `RuntimeError` that the `SupervisedListener._wrapped_callback` silently absorbed. Every phone reply was silently dropped; `dispatch_responses` never woke any `ask_human` future; pending count climbed forever (`/healthz` showed `total_answered: 0` against multiple pendings). Fix: bounce the coroutine into the event loop via `self._loop.call_soon_threadsafe(lambda c=coro: _spawn_bg(c, ...))` — matches the existing pattern used by `_on_combine`, `_on_force_end`, `_on_spawn`.
19. **Plugin hook scripts now read stdin as raw bytes.** All four hooks (`cli-session-injector-hook.py`, `cli-session-end-hook.py`, `agent-status-hook.py`, `turn-end-hook-away-mode.py`) were reading via `json.load(sys.stdin)` / `sys.stdin.read()`. Both go through `TextIOWrapper`, which on Windows defaults to **cp1252 with `errors='surrogateescape'`** when `PYTHONUTF8` isn't set in the *hook subprocess's* environment (Claude Code doesn't propagate it). Em-dash UTF-8 bytes `E2 80 94` got decoded as `â € "` (3 valid Latin-1 chars) → silent mojibake in tool input via `updatedInput`. Emoji bytes containing `0x81`/`0x8D`/`0x8F`/`0x90`/`0x9D` (e.g. 🐈 `F0 9F 90 88`) decoded to lone low-surrogate codepoints → exploded when the server tried to UTF-8-encode them for Firebase ("`'utf-8' codec can't encode character '\\udc90'`"). Fix in every hook: `payload = json.loads(sys.stdin.buffer.read())` — raw bytes, JSON spec mandates UTF-8 decode. Memorialized in [[windows-hook-stdin-must-read-bytes]].
20. **Plugin bumped 1.0.0 → 1.0.1 → 1.0.2.** First bump (1.0.0 → 1.0.1) was to invalidate the installed cache so the `cli-session-injector-hook` + `SessionEnd` hook entries that were missing from the old cache actually got installed. Second bump (1.0.1 → 1.0.2) pushed the stdin-bytes hook fix. Confirmed live: em-dash, multi-emoji messages, markdown rendering, and suggestion buttons all round-trip correctly.
21. **[scripts/verify/test3-hook-injection/run-test.ps1](scripts/verify/test3-hook-injection/run-test.ps1) forward-slash fix.** Project-local hook command on Windows is executed by Claude Code via `/usr/bin/bash` (bundled Git Bash); bash strips backslashes as escapes (`C:\Python314\python.exe` → `C:Python314python.exe` → command not found). Fix: convert both the python interpreter path and the hook script path to forward-slash form in the generated settings.json command. Restored Test 3 to PASS.
22. **`/combine_commands`, `/force_end_commands`, `/spawn_commands` now self-clean after dispatch** ([firebase.py](server/firebase.py)). Previously only `away_mode_commands` deleted entries after enqueueing; the other three command nodes accumulated stale entries in Firebase forever. The `if not path: return` filter in each listener correctly skips the bulk-load snapshot on server restart, so this was just data leakage rather than a functional bug — but the inconsistency was annoying in the Firebase console. Each `_on_*` callback now schedules a `db.reference(f"<node>/{path}").delete()` after dispatching the handler. At-most-once semantics (matches `away_mode_commands`); a server crash mid-handler loses the command and the phone would retry on user action.
23. **`resolve_wsl_home` gains `SWITCHBOARD_WSL_HOME` env-var escape hatch + diagnostic logging.** [main.py:resolve_wsl_home](server/main.py) silently returned `None` on every failure — so when the NSSM service running in Session 0 couldn't call `wsl.exe -e bash -lc "echo $HOME"` (a known Session-0-vs-WSL2 quirk), `wsl_home_resolved` ended up `None`, `set_global_wsl_available(False)` shipped to Firebase, and the phone's spawn dialog disabled WSL surface even on hosts where WSL is otherwise working. Fix: (a) honor `SWITCHBOARD_WSL_HOME` env var as a direct override (NSSM `AppEnvironmentExtra` can set it, bypassing the probe); (b) when the probe runs, surface exit-code / stderr / exception via the JSONL logger so future "wsl_available=false" mysteries are debuggable. Existing five `test_wsl_home.py` tests still pass — new `logger` parameter is optional, defaults to `None`. To use: `nssm set switchboard AppEnvironmentExtra +SWITCHBOARD_WSL_HOME=/home/<user>` then `nssm restart switchboard`.
24. **WSL spawn now uses a versioned static script + one-shot prompt file ([spawn-launcher.ps1](scripts/spawn-launcher.ps1) + new [scripts/spawn-claude-wsl.sh](scripts/spawn-claude-wsl.sh)).** Three iterations were attempted before this landing — earlier writeups in this document have been collapsed into this final entry.

    **The original failure:** the WSL branch built a long `$bashCmd` (single-quote bash escapes AND double quotes from prompt text like `"rpdm (windows)"`) and passed it inline to `Start-Process wt -ArgumentList …,"bash","-lc",$bashCmd`. PowerShell's quoting of the mixed-quote ArgumentList element couldn't survive wt's argument forwarding — wt treated the prompt text as the executable name and produced `[error 2147942402 (0x80070002) when launching `you don't need to provide it manually…`]`.

    **An intermediate base64 wrapper (`echo <b64> | base64 -d | bash`) failed silently** — empirically, wt does NOT preserve outer double-quoting when forwarding LONG quoted arguments to the new-tab process; wsl received the wrapper as multiple tokens (`echo`, the b64 payload, `|`, `base64`, …) and bash effectively ran a no-op echo, closing the tab without trace. (Also discovered along the way: wt parses `;` as its own command separator — `\;` to escape — which broke any wrapper that used `;` to chain shell commands.)

    **A second intermediate (write the whole bash command to `logs/spawn-script-<uuid>.sh` per spawn, invoke via `wt … bash -l <path>`)** worked but accumulated files in `logs/`.

    **Landed pattern:** versioned static script at [scripts/spawn-claude-wsl.sh](scripts/spawn-claude-wsl.sh) takes four positional args (`workspace`, `session-flag`, `session-id`, `prompt-file`). The launcher writes ONLY the prompt to `logs/spawn-prompt-<uuid>.txt`; the static script reads it then deletes it (no accumulation), then `cd`s into the workspace and invokes `claude` with the prompt + session args. `bash -l` makes it a login shell so `~/.bashrc` / `~/.profile` source and PATH includes user-scoped install locations like `~/.local/bin` where `claude` lives.

    Diagnostic logging on every spawn: `logs/spawn-launcher.log` (launcher's view: claimed pending files, surface, session id, Start-Process pid or error) and `logs/spawn-wsl.log` (script's view: start line with cwd / session id / distro / claude path / PATH dump, then exit line with code). Verified live: first WSL spawn under the new pattern landed clean — `claude=/home/janthony/.local/bin/claude` resolved, login shell PATH populated, no flash-and-disappear.

## Pre-merge validation (your hands required)

The implementation is in the working tree, NOT committed. Bulk commit gated on this:

1. **Re-run verification scripts** in [`scripts/verify/`](../scripts/verify/) — `test1-session-id.ps1`, `test2-resume.ps1`, `test3-hook-injection/run-test.ps1`.
2. **Phone-side smoke**: install APK; exercise Page A (one row per conversation, member roster, conv-id route), spawn (Win + WSL surfaces, create-new + add-to-existing), resume from long-press, combine, force-end, openConversation indicator, ⚠ stale-session warning, reply to ask_human, restart server (verify Ended conversations stay Ended, members_history restored, resume works for multi-member dormant convs).
3. **Wear-side smoke**: ChannelListScreen still works via derived projection. FCM notification tap navigates to the correct conversation.
4. **Away-mode round-trip**: phone toggle works. `ask_human` returns the at-desk sentinel when off; blocks normally when on.
5. **Agent-collab smoke**: two agents in one conv, one's session ends — peer wakes immediately (not after 24h). EMPTY return: caller is actually removed, conv transitions to Ended in Firebase.
6. **Force-end + dormant smoke**: a conv with one alive member + one dormant member (whose home was this conv). Force-end via phone. Verify after restart that the dormant member's session_home_conversation_id is cleared in Firebase under `cli_sessions/<sid>/home_conversation_id` (no stale pointer to the Ended conv), and that no orphan Active conversation was created for the dead session.
7. **Combine-with-source-waiter smoke**: Alice and Bob in source conv (Alice waiting in `message_and_await_agent`); Charlie in target conv. Combine source → target via phone. Verify Alice unblocks immediately (within seconds, not 24h) — either resolved by the post-combine `_wake_one_from(target)` or pending in target's queue and resolved on Charlie's first `message_and_await_agent` call.

## Open backlog

- **T-031**: Wear app full conversation-keyed migration (the FCM patch in Fix Pack 3 is a band-aid; full Wear migration retires `setupChannelsListener`, the projection, `selectedCwdKey` and the cwdKey-flavored Wear-compat shims).
- **T-001** (partial): pending-request write-behind + "we restarted; resume" broadcast. Note: Fix Pack 2 added `pending_questions` Firebase write; the restart-orphan cleanup sweep is the remaining piece.
- **T-029**: remove `reset_all_away_mode()` after Stop hook learns MCP-unavailable detection.
- **T-030**: re-evaluate per-conversation rate-limit parameters.
- **T-003**: stale-alive member GC.
- **Phase 4**: test gaps + remaining dead code sweep.

## Notes for next agent

- The implementation is in the working tree, NOT committed. Read `git status` for the full file list.
- Page A is conversation-keyed on phone; Wear still reads `/channels/<key>/*` via the projection.
- The 10-bug review revealed a recurring failure mode: **specced features that were never written**. Fix Pack 2's pending_questions subtree is one example; the design spec called for it, but no implementation existed. See [[feedback_spec_vs_implementation]] for the audit pattern.
- The pre-existing BOM in `tests/test_message_and_await_agent.py` was left intact — out of scope cleanup.
