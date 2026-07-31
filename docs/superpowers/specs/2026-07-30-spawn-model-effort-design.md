# Spawn model + effort selection (T-250) — design

**Date:** 2026-07-30
**Status:** approved (design pass with John, this session)
**Backlog:** T-250 (spawn model/effort pickers). Feeds T-251 (indicator) and T-252 (change-on-resume) but implements neither.
**Probe:** all CLI behavior claims verified live 2026-07-30 — see `docs/tracking/T-250-cli-flag-probe.md` (Claude Code 2.1.220, Antigravity CLI 1.1.8).

## Goal

The fresh-spawn dialogs (phone `+`, Operator spawn form) let John pick a model and an effort level for the new agent, with pick lists keyed to the chosen CLI. Blank picker = no flag = CLI default. Invalid combos fail loudly at spawn dispatch (never silently default), and resume paths preserve the spawn-time choices.

## Probe facts the design rests on

- Both CLIs take first-class launch flags: Claude Code `--model <alias|full-name>` + `--effort <low|medium|high|xhigh|max>`; Antigravity `--model <id>` (+ `--effort`, unused here).
- Claude Code silently ignores an invalid `--effort` (warns, runs at default, exit 0); its invalid-model error and all agy launch errors die inside the spawned wt tab. Dispatch-side validation is therefore mandatory.
- Claude Code `--resume` preserves the model but resets effort to the `effortLevel` settings default; `--effort` composes with `--resume`. So preserving effort across resume requires the server to record and re-pass it.
- Claude Code haiku has no effort concept (flag silently inapplicable).
- Antigravity bakes effort into its model ids (`gemini-3.6-flash-low`); `agy models` enumerates the live list; suffixed ids are valid `--model` values on their own; `--model`/`--effort` compose with `--conversation`, but an unknown conversation id silently starts fresh.

## Decisions (settled with John)

1. **Server-owned catalog, published to RTDB.** One catalog module server-side is the validation allowlist; the server publishes it to RTDB `spawn_options/` at startup; both clients render pickers from that node. Display list == validation list; no app redeploy when model lists change.
2. **Curated Claude Code list + live agy probe.** CC has no enumeration command → curated constant. `agy models` is exact → probed at startup with a curated fallback snapshot.
3. **Record model+effort, re-pass on resume.** Spawn-time choices are recorded on the session (SessionRecord + RTDB `sessions/`) and re-passed by all three resume paths. Sessions with no explicit choice record nothing and resume with CLI defaults, exactly like today.
4. **Pickers on fresh-spawn dialogs only.** Resume affordances stay silent (preserve recorded choices). T-252 later adds change-on-resume UI on top of this plumbing.
5. **agy gets a single Model picker, no Effort picker.** Effort is part of agy's model identity; offering the suffixed ids verbatim sidesteps the base-name+`--effort` composition (verified for only one family) entirely.

## Design

### 1. Catalog module — new `server/spawn_catalog.py`

- **Claude Code (curated constant).** Models in display order: `fable`, `opus`, `sonnet`, `haiku` (aliases; the CLI resolves each to the latest of its line). Efforts `low, medium, high, xhigh, max` for fable/opus/sonnet; `haiku` gets an empty effort list.
- **Antigravity (probed).** At startup, run `agy models` via `asyncio.to_thread` with a ~10s timeout. Output lines, stripped and non-empty, become the model list **verbatim** (no family parsing); every entry has an empty effort list. On failure (nonzero exit, timeout, agy missing): log loudly via `surface_error` and fall back to a curated snapshot of today's 11 ids.
- **Publication.** After the probe, the server writes the catalog to RTDB `spawn_options/` (full-node overwrite each startup):

  ```text
  spawn_options/
    published_at: "<ISO-8601>"
    claude/
      models: [ {id: "fable", efforts: ["low","medium","high","xhigh","max"]}, ..., {id: "haiku", efforts: []} ]
    antigravity/
      models: [ {id: "gemini-3.6-flash-high", efforts: []}, ... ]
  ```

  List order is display order. The same structure stays in memory as the dispatch validation allowlist.

### 2. Command schema + dispatch validation

- `spawn_commands` **fresh** entries gain optional string fields `model` and `effort`. Clients write them only when a non-default option is picked. Resume-type commands do NOT carry them (the server pulls recorded values, see §3).
- `handle_fresh` validates before any side effect (slotting in right after the existing project validation, before the quser gate and the away-mode auto-enable):
  - `agent=claude`: `model`, if present, must be a catalog id; `effort`, if present, must be a catalog tier; `effort` present with `model=haiku` → reject. `effort` without `model` is allowed and validated against the tier list — it applies to the CLI's default model, and if that default happens to be an effort-less model, Claude Code warn-ignores the flag (accepted edge; the default on this box is fable).
  - `agent=antigravity`: `model`, if present, must be a probed id; any `effort` → reject ("effort is expressed in the Antigravity model id").
- **Rejection is loud and launch-free:** `backend.send_text(...)` to the phone naming the bad value and the valid options (same pattern as the quser gate), plus an audit-log entry. No pending file, no away-mode flip.

### 3. Recording + resume re-pass

- `SessionRecord` gains `spawn_model: str | None` and `spawn_effort: str | None` — **deliberately distinct from the ring-fed `model` field**, which Watchtower sightings overwrite; the commanded choice must never be clobbered by the observed one.
- New `SessionRegistry.record_spawn_choice(cli_session_id, model, effort)` (`_ensure` + set + mirror). `handle_fresh` calls it after validation with the pre-assigned session id, and only when at least one of the two fields was picked — a default-everything spawn records nothing (per Decision 3).
- All three resume builders — `handle_resume` (conversation resume, per member), `handle_resume_session` (board resume), and `_spawn_pending_for_combine_resume` (`server/conversation_ops.py`) — look up the member's `SessionRecord` and thread `spawn_model`/`spawn_effort` into the pending-file agent entry. This re-passes `--effort` for Claude (which `--resume` otherwise resets) and `--model` explicitly for agy (whose resume preservation is unverified).
- **Resume re-validation is fail-soft:** if a recorded value is no longer in the catalog (e.g. agy retired a model), drop the invalid flag(s), log + `send_text` a notice, and launch with CLI defaults. A retired model must not make a session unresumable.

### 4. Launcher + WSL static scripts

- Pending-file agent entries gain optional `model` / `effort` fields (absent = unset).
- **Windows branch** of `scripts/spawn-launcher.ps1`: when present, append `--model '<m>'` / `--effort '<e>'` to the `$cli` string (both the claude and agy arms), single-quote-escaped like path/prompt.
- **WSL branch:** two new positional args after the prompt-file path — model, then effort — using the literal sentinel `-` for "unset" (never an empty string; empty tokens through wt's tokenization are the fragile case). `spawn-claude-wsl.sh` / `spawn-agy-wsl.sh` read `MODEL=${5:--}` / `EFFORT=${6:--}` and append the corresponding flag only when the value is not `-`; the start log line includes both values.
- **Mixed-version grace:** an old pending file consumed by the new launcher has absent fields → sentinel `-` → no flags; a new pending file hit by an old launcher ignores the extra fields. Both degrade to today's behavior.
- **No plugin version bump needed:** the launcher chain runs from repo paths (the SwitchboardSpawn scheduled task targets `$AppDir\scripts\spawn-launcher.ps1`; the WSL scripts are invoked via `/mnt/c/Work/Switchboard/scripts/`), not the plugin cache.

### 5. Android (phone dialog)

- New `SpawnOptions` DTO family in `network/Models.kt` (`@PropertyName`-annotated, defaults like its siblings). `MainViewModel` (shared module) gains a `spawn_options/` listener exposing `StateFlow<SpawnOptions?>`; no wear UI changes.
- `SpawnSessionDialog` gains two dropdowns below the Agent radio: **Model** and **Effort**, both defaulting to `Default (CLI)`. Rules:
  - Options come from the catalog keyed to the selected agent; switching agent resets both to Default.
  - `antigravity`: Effort dropdown hidden entirely.
  - `claude` with `haiku` selected: Effort dropdown disabled and forced back to Default.
  - Catalog absent (server down, node missing): both dropdowns render only `Default (CLI)`; spawn works exactly as today — nothing blocks on the catalog.
- Option derivation lives in a small pure helper (beside `ConversationPolicy`-style policy files) so it is unit-testable: `modelOptionsFor(options, agent)`, `effortOptionsFor(options, agent, modelId)`.
- `spawnSession(...)` gains nullable `model`/`effort` params, written into the command only when non-null. `resumeSession` / `resumeConversation` untouched.

### 6. Operator

- `schema.js`: `spawnOptions()` path builder. `firebase.js` + `store.js`: subscribe and project the node.
- `derive.js`: pure `modelOptionsFor` / `effortOptionsFor` mirroring the Android helper rules (agy → no effort select; haiku → disabled; missing catalog → Default-only).
- `commands.js`: `spawnFreshCmd` gains optional `model` / `effort` (absent unless picked).
- Spawn form (`components/StatusBar.js`, submitted through `store.js`): the same two selects with the same rules.

### 7. Testing

- **Server (pytest, new tests LF):**
  - Catalog: parse from a captured real `agy models` output; fallback on nonzero/timeout; publication payload shape.
  - Validation matrix: valid pairs accepted; unknown model / unknown effort / effort-on-haiku / effort-on-agy rejected with `send_text` and no pending file, no away-mode flip.
  - Pending-file threading: fields present/absent across all four spawn paths (fresh, resume, resume_session, combine_resume).
  - Resume re-pass from a recorded SessionRecord; fail-soft drop of now-invalid recorded values.
  - Hydration round-trip of `spawn_model` / `spawn_effort`.
- **Dashboard (`node --test`):** `spawnFreshCmd` field presence/absence; `modelOptionsFor` / `effortOptionsFor` matrix; store projection of `spawn_options`.
- **Android:** unit test for the pure options helper. Dialog stays manual (no androidTest infra — T-209).
- **Live checks (John-gated, after service restart):** one phone spawn with model+effort picked (tab launches with flags; transcript shows the effort); one resume of that session (effort survived); one hand-written invalid `spawn_commands` entry (loud phone message, no launch).

### 8. Docs + tracking

- README spawn section + comprehensive design spec: new command fields, `spawn_options/` node, recording/resume semantics.
- `SKILL.md` untouched (no agent-facing tool changes).
- Close-out per the usual workflow: backlog T-250 → completed-ledger, PROJECT-JOURNAL entry, roadmap fold-in.

## Out of scope

- T-251 (model/effort indicator in client views) — this design records the data but builds no display.
- T-252 (change model/effort on resume) — the plumbing here makes it a pure-UI follow-up.
- Wear UI, mid-session model switching, agy base-name+`--effort` composition, effort for agy.

## Error handling summary

| Case | Behavior |
| ---- | -------- |
| Invalid model/effort on fresh spawn | Loud `send_text` + log; no launch, no side effects |
| Recorded value invalid at resume | Drop flag(s), `send_text` notice + log, launch with CLI defaults |
| `agy models` probe fails at startup | `surface_error` log; curated fallback snapshot published |
| Catalog node missing on a client | Pickers render Default-only; spawn unaffected |
| Old/new launcher-payload mix | Degrades to no-flags behavior (sentinel `-` / ignored fields) |

## As built (implemented 2026-07-30; status: complete, pending John's commit)

Implemented as designed except where noted. Every item below is a place where the shipped code
or the live system differs from this document as originally written.

**Catalog and publication**

1. `probe_agy_models` uses `asyncio.create_subprocess_exec`, not the `asyncio.to_thread` named in
   section 1. Same timeout semantics, natively async.
2. The probe kills the child on timeout (`proc.kill()` + `await proc.wait()`), added after review:
   `asyncio.wait_for` cancels `communicate()` but does not reap the process, so the original form
   leaked it. Residual, accepted: external cancellation still orphans the child, because
   `CancelledError` is a `BaseException` and bypasses both handlers. Not reachable today, since no
   caller wraps the probe in its own timeout.
3. Worst case the agy probe adds about 10 seconds to startup (`AGY_PROBE_TIMEOUT_SECONDS = 10.0`
   on the boot path) if the binary hangs rather than failing fast.
4. **The probe does not work on this deployment, and this is the most important as-built fact in
   this document.** The service runs as `LocalSystem` under NSSM and `agy` is not on that
   account's `PATH`, so the probe fails at *every* startup with
   `spawn_catalog_agy_probe_failed: [WinError 2] The system cannot find the file specified` and the
   published Antigravity list is permanently the curated fallback snapshot. Verified live
   2026-07-30 by reading `logs/switchboard.jsonl` after a service restart. The feature works and
   the failure is loud, but the list will go silently stale whenever Antigravity changes its
   line-up, which is precisely the drift Decision 2's probe existed to prevent. Do not describe
   the shipped Antigravity list as live-probed. Two candidate fixes, neither chosen here: resolve
   `agy`'s absolute path from config, or push the list from a user session the way Watchtower
   pushes telemetry (the D6-consistent shape). This is an instance of the standing D6 constraint:
   the server cannot see John's user-profile world.
5. **Realtime Database does not store empty arrays, so section 1's payload example is not literally
   what lands.** `efforts: []` is dropped entirely: the real node contains `{"id": "haiku"}` and all
   eleven Antigravity entries carry no `efforts` key. Verified live by fetching the node. This is
   harmless and was verified rather than assumed: Operator coalesces via its `Array.isArray` guard
   and its `(m && m.efforts) || []` union branch, Android via `List<String>? = null` plus
   `?: emptyList()`, and the server is unaffected because it validates the in-memory catalog, which
   does retain `efforts: []`, and never reads the published node back.

**Recording and resume**

6. `record_spawn_choice` gained an optional `cwd` parameter that seeds an empty record's `cwd` and
   `surface`. Without it the session-board row reads "(unknown)" while the spawn is in flight, since
   this call creates the roster record before the agent's own `SessionStart` hook lands. The
   `handle_fresh` call site passes `cwd=project_path`.
7. Section 3's "all three resume builders" is accurate but reads like a gap: `handle_resume_session`
   delegates to `launch_resume_agent`, so the board-resume path is covered by the same code rather
   than by a third edit.
8. The three resume-path dropped-choice notices wrap `send_text` in `try`/`except` with distinct
   `surface_error` labels (`spawn_resume_notice_send_failed`, `resume_session_notice_send_failed`,
   `combine_resume_notice_send_failed`). Unguarded, a failing phone send could propagate and defeat
   the fail-soft guarantee the notice exists to report, in one case skipping a rollback.
9. Known and accepted residual, filed as backlog T-260 rather than fixed here: the resume gate
   accepts never-launched spawn-created session records. A picked spawn that dispatches but whose
   agent never starts leaves a record that the 900-second sweep marks `lost`, and because item 6
   gave it a non-empty `cwd` it then satisfies the resume gate. Tapping Resume fires `--resume` on a
   session id that never existed. Only picked spawns can do this; item 6's seeding is what made it
   resumable.

**Testing, beyond what section 7 specified**

10. `tests/test_firebase_paths.py` gained `test_publish_spawn_options_writes_full_node`. Section 7
   already required "publication payload shape"; the implementation plan omitted it.
11. `dashboard/derive.test.js` gained two Antigravity assertions. The original single assertion used
   an agy id absent from Claude's list, which returns `[]` whether or not the Antigravity guard
   exists, so it could not detect the guard's removal. Proven by counterfactual.
12. `SpawnOptionsPolicyTest` gained a fixture mirroring the live payload plus four tests for the
   `efforts=null` and `models=null` shapes. Item 5 showed those are the primary production shapes,
   not edge cases, so leaving them covered only by a hand-trace was not acceptable.

**Verification status**

- Server suite 1076 passed, Operator 167 passed, Android shared module 128 tests with 0 failures,
  `:app:assembleDebug` successful. A full `./gradlew build` fails at `:wear:lintDebug` on a
  pre-existing, unrelated error (`InvalidFragmentVersionForActivityResult`) that is dispositioned to
  the backlog; the per-module gates above were run in its place.
- Live-verified after a service restart: the node publishes, listeners are all live with zero
  crashes, and every pick Operator can produce validates against the real server validator (37
  reachable payloads, 0 rejected, with negative controls confirming the validator does reject).
- Verified at review time by live execution: `wt.exe` re-tokenizes the two new WSL positional args
  correctly. The final whole-branch review ran it in four cases - a picked `--model` / `--effort`
  pair, the `-` / `-` sentinel yielding zero extra arguments, a mixed pair, and an old four-arg
  payload for backward compatibility. This was the historically-bitten layer, so it is worth
  noting explicitly; remaining confirmation is incidental to John's first picked WSL spawn.
- Not yet verified, and requiring John, a phone, and a real spawn: an end-to-end picked spawn and
  resume, the loud rejection of a hand-written invalid command, and whether the CLIs tolerate
  `--model` / `--effort` immediately following `--dangerously-skip-permissions` (the flags compose
  with `--session-id` / `--resume` / `--conversation` per the probe, but this exact adjacency is
  live-unconfirmed and, being a launch-time error, would die inside the spawned tab).
