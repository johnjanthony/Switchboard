# Remove agy: the Antigravity CLI, its hooks, its spawn path and its status lamp (design)

**Date:** 2026-10-12
**Status:** design approved by John 2026-10-12 in chat; written spec awaiting his review.
**Backlog:** T-283 (this work). Discharges T-261 (the agy model probe) and T-277's marker-count bullet. T-249 stays open with its premise refreshed (decision 2).

## Goal

John abandoned Antigravity (agy) on 2026-10-03 (Claude Code mod spec, decision 1), and Switchboard has served Claude Code through the mod since. Remove every agy code path, every file only agy reads, and the Antigravity service-status lamp, across the server, scripts, Operator, the phone, Watchtower, the plugin and the docs, without changing anything a Claude Code session sees.

Done means: no agy branch left in the tree outside the history listed under Docs and tracking; every suite green; the deployed service, Operator, phone and Watchtower showing no Antigravity UI; and agy's own config on both machines no longer pointing at Switchboard.

## Decisions (John, 2026-10-12)

1. **The Antigravity status lamp goes too.** It watches GCP incidents for Gemini and Vertex AI, which matter only to an agy user.
2. **T-249 stays out.** The sweep's agy-only lost-to-dormant branch is deleted, not generalized. A lost desktop session can still be alive (T-290), so making a lost Claude session's member dormant needs its own design for what a later poll does to that member.
3. **Claude cleans agy's config outside the repo**, on Windows and WSL.
4. **One chunk, one deploy.** Every client is John's own device, and a server-only first half would leave a frozen lamp on the clients and a spawn option that is only ever rejected.

## Facts the design rests on (verified 2026-10-12 at 090c45c)

- The root `hooks.json`, `mcp_config.json` and `plugin.json` are agy's plugin (`agy plugin install <repo>`). The Claude Code marketplace (`.claude-plugin/marketplace.json`) names `./plugin`, whose manifest is `plugin/.claude-plugin/plugin.json`, so no Claude Code session reads the root files.
- The three Python hook scripts left in `scripts/` (`agy-identity-hook.py`, `turn-end-hook-away-mode.py`, `_hook_common.py`) are called only by agy: by the root `hooks.json`, and by `~/.gemini/config/hooks.json`, whose Windows copy has a `switchboard` block calling `C:/Work/switchboard/scripts/agy-identity-hook.py` and `turn-end-hook-away-mode.py --cli antigravity`. WSL has a `~/.gemini/config/hooks.json` too. Neither is chezmoi-managed. agy's Windows plugins directory lists `switchboard` beside `cerebro`, `superpowers`, `firebase` and `android-cli-plugin`, so agy itself stays installed.
- The marker sweep (`dispatch_session_end_markers`) reads `logs/session-end`, a directory `server/main.py` derives from the log path. Claude Code sessions end through `POST /session_end` since the mod, and agy's hooks never wrote markers. Its only remaining writer is the smoke harness, whose session-end flow writes a marker (`write_session_end_marker`, `scripts/smoke/_smoke_lib.py`) and waits for the sweep. The harness's restart-survival flow also reads its parked-answer notice through the `/away-mode` `session_id` pop.
- Once the catalog has no `antigravity` key, `validate_spawn_choice` raises `KeyError` for `agent: "antigravity"`, which `dispatch_spawn_commands` reports as a crashed command, not a rejection. Convene-by-resume (`_perform_convene`) relaunches ended or lost session records through `launch_resume_agent`, so it can reach an old agy record too.
- `/widget-status` resolves its `target` query parameter silently: any value other than `antigravity` gets the Claude view.
- `publish_spawn_options` (`server/firebase.py`) overwrites the whole `spawn_options` node at startup, so its `antigravity` key goes with the first publish. Nothing overwrites `widget/antigravity_status`.
- Nothing produces `quota.antigravity` any more: Watchtower's Antigravity-quota reader was deleted in `a7b4ac0`. The phone's and Operator's readers of it are dead code.
- `post_agent_message` and the `message_and_await_agent` timeout clamp were built because agy's `call_mcp_tool` froze the whole session while it waited. Claude agents use both in collab.
- A fresh spawn validates its pick (`validate_spawn_choice`) before it turns away mode on, and the resume paths run their gates before that point too (`server/spawn.py`).

## What goes

### Server

- `server/antigravity_status.py`; its construction and wiring in `server/main.py`; the `target=antigravity` branch of `/widget-status`, which now answers 400 for any `target` other than `claude` (or none), so an old client asking for the Antigravity view fails loudly; `write_widget_antigravity_status` in `server/firebase.py`; the Antigravity half of `check_all` and `stop_all` in `dispatch_status_request_commands` (the phone still sends both types, which now drive the Claude service alone).
- The marker sweep: `dispatch_session_end_markers`, its task, its supervisor entry, the marker-directory setup, and any counter or health field only it feeds. `handle_session_end` (`server/cli_session_end.py`) stays, because the `/session_end` route uses it.
- The agy UserPromptSubmit notice pop in `/agent_status`, and the `session_id` notice pop in `/away-mode`. The `/away-mode` GET that reports the flag stays.
- `_propagate_agy_lost_to_members` and its call in the session sweep.
- In `server/spawn.py`, the agy launch and resume branches (`_is_antigravity_session` and the `agent_kind` / `agent_label` forks). In `server/spawn_catalog.py`, `probe_agy_models`, `parse_agy_models`, the agy fallback ids and the catalog's `antigravity` key; startup builds the catalog without a probe.
- The "Antigravity" default sender in `server/session_fallback.py`, and the Gemini display-name handling in `server/model_label.py` (an old agy record shows its raw model id).
- The Antigravity wording in the MCP tool descriptions (`server/main.py`) and in the handlers' error text (`server/gateway/handlers.py`). The `cli_session_id` and `cwd` parameters stay, because the mod injects them.

### Scripts and root files

- `scripts/agy-identity-hook.py`, `scripts/turn-end-hook-away-mode.py`, `scripts/_hook_common.py`, `scripts/spawn-agy-wsl.sh`, and the agy branch of `scripts/spawn-launcher.ps1`.
- The root `hooks.json`, `mcp_config.json` and `plugin.json`.
- The smoke harness ends its session with `POST /session_end` in place of `write_session_end_marker`, and its flow waits for the same `ended` state. Its restart-survival flow reads the parked-answer notice from `GET /sessions/{sid}/inbox`.

### Operator (`dashboard/`)

- The Agent picker in the spawn dialog; spawns always send `agent: "claude"`.
- The Antigravity status pill (`AntigravityStatusControl` in `StatusBar.js`), `requestAntigravityStatus` and `setWidgetAntigravityStatus` in `store.js`, `widgetAntigravityStatus` in `schema.js`, and the `antigravity-pill` styles.
- The agent branch of the spawn-option helpers in `derive.js`, and the agy data in `demo.js`. (Operator's `quota.antigravity` reader already went in `12b52da`.)

### Phone (`android/`)

- The Agent picker in `SpawnSessionDialog`, and the agent parameter and branches in `SpawnOptionsPolicy`.
- The `widget/antigravity_status` listener and the `widgetAntigravityStatus` flow in `MainViewModel`, and the lamp: `AntigravityStatusPillChip` in `MainActivity.kt`, placed by `ConversationListScreen`.
- The dead agy quota rows in `WidgetStatusHeader` and `QuotaDetailDialog`; `groupSortKey`, `formatAgyGroupName` and `isAgyGroupVisible` in `WidgetFormat`; `WidgetQuotaGroup` and the `antigravity` fields in `network/Models.kt`.

### Watchtower

- `AntigravityStatusReader`, `AntigravityStatusView` and `AntigravityStatusViewTests`; the lamp in `WidgetWindow`, `DetailPanel`, `TrayIcon`, `Palette` and `AppHost`; the Antigravity note in `SessionModel`.

### Plugin

- The agy guidance in `plugin/skills/switchboard/SKILL.md`: the `continue`-steering note, "Antigravity" as a sender example, and the `call_mcp_tool` collab pattern, rewritten harness-neutrally because another section points at it. The comment in `plugin/hooks/turn-end.ts` that names the deleted `scripts/turn-end-hook-away-mode.py`.

## Compatibility with old clients and old data

- **Spawns and resumes that name agy.** A fresh spawn with `agent: "antigravity"` (from an APK or an Operator tab older than the deploy), a resume of a session whose record says `cli: "antigravity"`, and a combine-resume of such a member are each rejected loudly, the way an invalid model pick is today: a `send_text` to the phone and an audit entry, before away mode is touched, with no tab opened. A combine that would relaunch such a member aborts whole, moving nothing. Convene-by-resume skips such a record with a reason in its skipped list. The checks are generic (any `agent` or `cli` other than `"claude"`), so the server code names no retired CLI.
- **Old records.** `sessions/` records and conversation members with `cli: "antigravity"` keep hydrating and rendering, since `cli` is display data. They age out under the existing retention rules.
- **RTDB leftovers.** `widget/antigravity_status` is deleted once, by hand, during the deploy. It is a one-time migration, so it gets no permanent code. `spawn_options/antigravity` goes with the first startup publish.

## Outside the repo (Windows and WSL)

Done as the first task, so agy's config never names a deleted script. On each machine, read `~/.gemini/config/hooks.json` and `~/.gemini/config/mcp_config.json`, back both up to the session scratch directory, remove only their `switchboard` entries, then remove the installed `switchboard` agy plugin with agy's own uninstall command. agy and its other plugins stay. Confirm afterwards that `agy plugin list` no longer shows `switchboard`.

## Docs and tracking

- **Updated:** `docs/switchboard-design-spec-comprehensive.md`; `README.md` (its Antigravity CLI subsection and the spawn section); `CLAUDE.md` (the Layout entries for `spawn_catalog.py`, `cli_session_end.py`, `gateway/dispatch.py` and `agy-identity-hook.py`, Setup step 3, the Hooks section's agy paragraph and marker mentions, and the MCP section's non-Claude note); `watchtower/README.md`; `docs/media/switchboard-architecture.svg` (its agy box, arrow and marker-sweep label).
- **Unchanged, as history:** the dated specs under `docs/superpowers/specs/`, `PROJECT-JOURNAL.md`, and `.graphify-verdicts.json` (the src-view rebuild reports verdicts for deleted code as stale and keeps them).
- **Backlog:** cut T-283 and T-261. Remove T-277's marker-count bullet. Refresh T-249's premise: the mod's `/session_end` replaced the marker, so any hard-killed session now leaves its member alive, and the agy branch it cites is gone. Trim the agy asides in T-252, T-260 and any other entry that still names agy.
- **Journal:** a PROJECT-JOURNAL entry. **Memory:** retire `project_agy_spawn_support`.

## Testing

- **Deleted:** `tests/test_agy_identity_hook.py`, `tests/test_hook_common.py`, `tests/test_turn_end_hook.py`, `tests/test_session_end_markers.py`, `tests/test_antigravity_status.py`, and `AntigravityStatusViewTests.cs`. Mixed test files lose their agy cases.
- **Added (server).** The compatibility tests use a neutral non-claude value (`"retired-cli"`), which exercises the same generic checks an old agy record or client hits:
  - A fresh spawn naming a non-claude agent is rejected with a phone notice and leaves away mode, the session registry and the launcher untouched.
  - A resume, a resume-session and a combine of a non-claude record are rejected the same way; convene skips it.
  - A hydrated non-claude session record still loads.
  - `/away-mode?session_id=<sid>` returns the flag and leaves that session's notices queued.
  - `/agent_status` pops no notices, whatever the `cli`.
  - The built catalog holds only the `claude` key.
  - `/widget-status` answers 400 for an unknown `target`; `check_all` and `stop_all` drive the Claude service.
- **Gates:** `pytest` with an explicit fresh `--basetemp`, `node --test dashboard/*.test.js`, `dotnet test` from `watchtower/`, `:shared:testDebugUnitTest` plus the app compile, and the mod checks (`claude plugin test plugin` and `scripts/mod-typecheck.sh`, which `tests/test_mod_plugin.py` also runs).
- **Final sweep:** a case-insensitive grep (`git grep -I -i`) for `agy`, `antigravity` and `gemini` across tracked text files finds nothing outside the history listed under Docs and tracking and `.gitignore`'s `/GEMINI.md`.

## Deploy and live checks

John-gated where the standing rules say so.

1. Restart the service (`restart-service.ps1 -SkipTests`), then `smoke.py --preflight-only`, then the smoke run (with `--skip-restart` when other agents are live). Its session-end flow now goes through `POST /session_end`.
2. Delete `widget/antigravity_status` from RTDB.
3. Refresh Operator: no Agent picker, no Antigravity pill, and the spawn dialog still lists the Claude models and efforts.
4. Phone: check the spawn dialog and the status header on the Pixel_10_Pro emulator, then install the release APK on the phone at John's go.
5. Watchtower: `deploy-widget.ps1`. The widget, hover popup and tray menu show no Antigravity lamp.
6. `scripts/deploy-mod.ps1` after the commit that touches `plugin/`.
7. `/healthz`: no marker-sweep loop and no Antigravity entries.

## Out of scope

- T-249 (decision 2) and T-277's other bullets.
- Pruning the stale `.graphify-verdicts.json` entries.

## As built (2026-10-12)

Commits on `develop`, by task:

- Spec and grounding: `1583773`, `242200e`.
- Task 1 (outside the repo): agy's switchboard plugin and hook config removed on Windows and WSL; no repo commit.
- Task 2, status lamp and `/widget-status` target: `a7e3d2e`.
- Task 3, marker sweep, notice pops, agy dormancy, smoke harness: `bb91d97`.
- Task 4, spawn, resume, combine and convene Claude-only, catalog probe dropped: `d1b5105`.
- Task 5, hook scripts, root files and tool text: `6552016`.
- Task 6, Operator: `86a778b`.
- Task 7, Android: `0f9ade3`, then the KDoc fix round `73f4c3f`.
- Task 8, Watchtower: `c43f9cb`.
- Task 9, docs: `ccfdf46`.
- Tidy, five stale sentences: `4254cdd`.
- Task 10, live checks: no commit.

Behaviours that replaced the agy paths:

- Spawn, resume, resume-session, combine and convene refuse any agent or session cli other than `claude`, loudly and before any side effect (convene skips the record with a reason).
- `/widget-status` answers 400 for any target other than `claude`.
- `/away-mode` and `/agent_status` never pop a session's notices; the mod's inbox poll is the only consumer.
- The smoke harness ends its session through `POST /session_end` and reads its parked-answer notice from the inbox.

Deviations from the plan:

- Work ran in the main checkout on `develop`, with no worktree.
- `tests/test_stats_endpoint.py` (sender "Gemini") belonged to no task and went to Task 4 (it now says "Claude WSL").
- Task 7's final review found a KDoc sentence left half-rewritten (`MainViewModel.kt`, "the Anthropic and service status"); one fix round corrected it (`73f4c3f`).
- A tidy commit (`4254cdd`) took the five one-line inaccuracies the final review turned up: the `queue_notice` docstring, `demo.js` msg-003, a `test_main_routes.py` docstring, a stray space and a "claude only;" row in the comprehensive spec.
- Outside the repo, the dotfiles repo (`40b1cd2`) dropped the Gemini collab command and made the GEMINI.md line Claude-only; the WSL `collab.toml` was removed by hand. The `link_gemini_switchboard` chezmoi scripts were left in place.
- Task 11 ran after the final review, so this record includes it.

Final review notes (Ready, no Critical or Important):

- The plan's Review Focus 1, "away mode stays as it was", holds server-side only. An old APK flipped away mode on client-side before the server's rejection, as it does for an invalid pick. That closed when the release APK was installed on John's phone, which it now is.
- The smoke session now polls the inbox, so a `--keep` run's residue goes lost after about 90 s.
- An empty `logs/session-end/` was removed by hand.

Live checks passed: preflight, `/widget-status` (400 for an unknown target), `/away-mode` with no notices, Operator, emulator and phone, Watchtower, and `smoke.py --skip-restart` (exit 0). The RTDB node `widget/antigravity_status` was deleted.

Still open:

- T-249's premise was refreshed in the backlog (the marker sweep is gone, so any session killed without a `POST /session_end` leaves its member alive); the fix is not designed.
- The smoke restart-survival flow was skipped by `--skip-restart`, so its new inbox read is not live-verified.
- The `link_gemini_switchboard` chezmoi scripts remain, and `C:\Work\Claude-Scratch\T-283-agy-config-backup\` awaits John's call on deletion.
