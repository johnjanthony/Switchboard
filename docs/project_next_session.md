# Next-session resumption notes — `cwd-as-channel` branch

Read this first when picking up the `cwd-as-channel` branch in a fresh session. The branch is in PR-ready shape pending one final commit and the merge decision.

## Status (2026-04-26)

The cwd-as-channel migration (Slices A–L of the 2026-04-24 spec) shipped in earlier commits. Slice M validation was walked end-to-end today on phone and watch; most scenarios pass, the rest are non-blocker gaps captured as separate backlog items.

## What landed today (commits on this branch, newest first)

- *(latest commit)* — test fix + one new backlog entry. `tests/test_away_mode_commands.py` `FakeBackend` gains a `write_channel_message` stub (now required by the post-`a84d690` `bulk_respond_send_to_all` chat-history write), and `_run_one_cmd` iteration count bumped 5 → 50 since the bulk-respond fan-out adds enough awaits to need more yields. `docs/feature-backlog.md` gains *"`exit_global` partial-failure leaves global away stuck"* (Server) — a latent production concern surfaced by debugging the test failure: in `gateway.py:624-647`, if the bulk-respond fan-out raises, the outer except clears the dialog but `set_global_away(False)` is never reached, so the user's exit-toggle silently fails.
- `0bcec97` — `decision` UnboundLocalError fix + doc cleanup. `gateway.py` initializes `decision = {"action": "skip"}` before the `if payload["sections"]:` block so the trailing log statement can't raise; reply-mirror sender renamed `"Human"` → `"John"`. `.gitignore` picks up `.artifacts/*` and `__work*.log`. `docs/feature-backlog.md` reorganized post-Slice-M (Server/Client/Combined sections, stale entries removed, three new ones added). `PROJECT-JOURNAL.md` 2026-04-23 entry now notes it superseded the older Silence Detection idea. `docs/project_next_session.md` rewritten for resumption.
- `a4410e2` — restore suggestion buttons rendering on the phone (regression from the cwd-as-channel UI rewrite).
- `c76332d` — three correctness fixes for spawn + away-mode:
  1. **Spawn no longer flips global away.** `_handle_single_spawn` / `_handle_collab_spawn` now call `registry.set_cwd_override(canonical_cwd, True)` instead of `set_global_away(True)`. Previously, every spawn wiped any explicit per-cwd at-desk overrides the user had set on other channels.
  2. **Spawn channel routing uses canonical cwd.** `_make_channel_id(project_key)` removed; both spawn paths use `canonicalize_cwd(str(project_path))`. Eliminates the duplicate-orphan `project-YYYYMMDD-HHMMSS` channel previously created on every spawn.
  3. **Mirror sync on bulk-clear.** When `set_global_away` clears `_cwd_overrides`, the registry now fires per-cwd callbacks with `active=None`; `FirebaseBackend.write_away_mode_mirror` deletes the override node on `None`. Closes the long-standing divergence where Firebase retained stale override entries invisible to the local Registry.

## Slice M validation outcomes

| # | Scenario | Result |
|---|---|---|
| 1 | Spawn into fresh cwd | ✓ |
| 2 | Spawn collision Continue | ⚠ blocked → backlog *Wire spawn-collision dialog into the `/spawn` command path* |
| 3 | Spawn collision Clear | ⚠ same as 2 |
| 4 | Per-channel pill toggle | ✓ |
| 5 | Global pill + bulk-respond | ✓ (surfaced `decision` UnboundLocalError, fixed in pending commit) |
| 6a/b | Reply round-trip | ✓ |
| 6c | `responses/` slot lifecycle at idle | ✓ (ephemeral by design — empty is correct) |
| 6d | Withdrawn-question on agent death | ⚠ pre-existing transport gap → backlog *Withdraw pending questions when the agent process dies* |
| 7 | Title rendering Page A + B | ✓ |
| 8 | Hidden channel hide/show/unhide | ✓ |
| E | FCM tap deep-link | ✓ (note: Android Force Stop suppresses FCM until app relaunch — methodology trap, not a bug) |
| F | BYO collab two senders | ✓ mechanically; UX gap → backlog *Android: multi-sender reply UX in BYO collab channels* |

## Next steps

1. **Verify state**:
   ```
   git status                     # should be clean
   python -m pytest tests/ -q     # 321 should pass
   cd android && ./gradlew :app:compileDebugKotlin :wear:compileDebugKotlin
   ```

2. **Decide whether to merge to main.** Open backlog items are follow-ups, not regressions. If merging, this file should be deleted (or rewritten for whatever the next branch is).

3. **Or take on a backlog item.** Top candidates surfaced this session:
   - *Wire spawn-collision dialog into the `/spawn` command path* (Server, multi-file).
   - *Android: multi-sender reply UX in BYO collab channels* (Client, pure UX).
   - *Withdraw pending questions when the agent process dies* (Combined; recommended approach: cancel-on-spawn).
   - *Android: swipe gestures on channel rows* (Client, pure UX).
   - *Away-mode Firebase schema reorganization* (Combined, schema cleanup).

## Design memory — non-obvious things future-you might re-question

- **`set_global_away` wiping `_cwd_overrides` is by design** (`registry.py:135`). Confirmed by user 2026-04-26: changing the global setting trumps per-channel state; individual channels can override after each global change but only until the next global change. **Spawn must NOT call `set_global_away`** — use `set_cwd_override(canonical_cwd, True)` instead.
- **MCP streamable-HTTP transport doesn't reliably surface mid-call client disconnects** to in-flight tool handlers on the server. This is the root cause of the test 6d cancellation gap and may affect any other "client died mid-call" scenario. `asyncio.CancelledError` in `gateway.ask_human` only fires on graceful shutdown or explicit supersede.
- **The `responses/{cwdKey}__{sender}` slot is ephemeral.** Phone writes it, server reads + deletes it via `send_resolution_confirmation` within milliseconds. Empty `responses/` node at idle is correct.
- **Spawn-collision plumbing exists but isn't wired** to the `/spawn` command path. `SpawnHandler.submit()` and `resolve_collision()` look correct; `_handle_spawn` simply doesn't invoke them. See backlog entry for the multi-file fix scope.

## Reference

- Spec: `docs/superpowers/specs/2026-04-24-cwd-as-channel-and-per-cwd-away-mode-design.md`
- Plan: `docs/superpowers/plans/2026-04-25-cwd-as-channel-and-per-cwd-away-mode.md` (gitignored)
- Backlog: `docs/feature-backlog.md` (organized as Server / Client / Combined since 2026-04-26)
- Journal: `PROJECT-JOURNAL.md`
