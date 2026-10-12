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
- The marker sweep (`dispatch_session_end_markers`) reads `logs/session-end`, a directory `server/main.py` derives from the log path. Claude Code sessions end through `POST /session_end` since the mod. The sweep's remaining users are agy's classic hooks and the smoke harness, whose session-end flow writes a marker (`write_session_end_marker`, `scripts/smoke/_smoke_lib.py`) and waits for the sweep.
- `publish_spawn_options` (`server/firebase.py`) overwrites the whole `spawn_options` node at startup, so its `antigravity` key goes with the first publish. Nothing overwrites `widget/antigravity_status`.
- Nothing produces `quota.antigravity` any more: Watchtower's Antigravity-quota reader was deleted in `a7b4ac0`. The phone's and Operator's readers of it are dead code.
- `post_agent_message` and the `message_and_await_agent` timeout clamp were built because agy's `call_mcp_tool` froze the whole session while it waited. Claude agents use both in collab.
- A fresh spawn validates its pick (`validate_spawn_choice`) before it turns away mode on, and the resume paths run their gates before that point too (`server/spawn.py`).

## What goes

### Server

- `server/antigravity_status.py`; its construction and wiring in `server/main.py`; the `target=antigravity` branch of `/widget-status`; `write_widget_antigravity_status` in `server/firebase.py`; the Antigravity half of `check_all` and `stop_all` in `dispatch_status_request_commands`.
- The marker sweep: `dispatch_session_end_markers`, its task, its supervisor entry, the marker-directory setup, and any counter or health field only it feeds. `handle_session_end` (`server/cli_session_end.py`) stays, because the `/session_end` route uses it.
- The agy UserPromptSubmit notice pop in `/agent_status`, and the `session_id` notice pop in `/away-mode`. The `/away-mode` GET that reports the flag stays.
- `_propagate_agy_lost_to_members` and its call in the session sweep.
- In `server/spawn.py`, the agy launch and resume branches (`_is_antigravity_session` and the `agent_kind` / `agent_label` forks). In `server/spawn_catalog.py`, `probe_agy_models`, `parse_agy_models`, the agy fallback ids and the catalog's `antigravity` key; startup builds the catalog without a probe.
- The "Antigravity" default sender in `server/session_fallback.py`, and the Gemini display-name handling in `server/model_label.py` (an old agy record shows its raw model id).
- The Antigravity wording in the MCP tool descriptions (`server/main.py`) and in the handlers' error text (`server/gateway/handlers.py`). The `cli_session_id` and `cwd` parameters stay, because the mod injects them.

### Scripts and root files

- `scripts/agy-identity-hook.py`, `scripts/turn-end-hook-away-mode.py`, `scripts/_hook_common.py`, `scripts/spawn-agy-wsl.sh`, and the agy branch of `scripts/spawn-launcher.ps1`.
- The root `hooks.json`, `mcp_config.json` and `plugin.json`.
- The smoke harness ends its session with `POST /session_end` in place of `write_session_end_marker`, and its flow waits for the same `ended` state.

### Operator (`dashboard/`)

- The Agent picker in the spawn dialog; spawns always send `agent: "claude"`.
- The Antigravity status pill (`AntigravityStatusControl` in `StatusBar.js`), `requestAntigravityStatus` and `setWidgetAntigravityStatus` in `store.js`, `widgetAntigravityStatus` in `schema.js`, and the `antigravity-pill` styles.
- The agent branch of the spawn-option helpers in `derive.js`, the `quota.antigravity` reader, and the agy data in `demo.js`.

### Phone (`android/`)

- The Agent picker in `SpawnSessionDialog`, and the agent parameter and branches in `SpawnOptionsPolicy`.
- The `widget/antigravity_status` listener and the `widgetAntigravityStatus` flow in `MainViewModel`, and the lamp in `WidgetStatusHeader`.
- The agy groups in `QuotaDetailDialog`, `formatAgyGroupName` and `isAgyGroupVisible` in `WidgetFormat`, and the `antigravity` fields in `network/Models.kt`.

### Watchtower

- `AntigravityStatusReader`, `AntigravityStatusView` and `AntigravityStatusViewTests`; the lamp in `WidgetWindow`, `DetailPanel`, `TrayIcon`, `Palette` and `AppHost`; the Antigravity note in `SessionModel`.

### Plugin

- The agy guidance in `plugin/skills/switchboard/SKILL.md`: the `continue`-steering note, "Antigravity" as a sender example, and the `call_mcp_tool` collab pattern. The agy comment in `plugin/hooks/turn-end.ts`.

## Compatibility with old clients and old data

- **Spawns and resumes that name agy.** A fresh spawn with `agent: "antigravity"` (from an APK or an Operator tab older than the deploy), a resume of a session whose record says `cli: "antigravity"`, and a combine-resume of such a member are each rejected loudly, the way an invalid model pick is today: a `send_text` to the phone and an audit entry, before away mode is touched, with no tab opened.
- **Old records.** `sessions/` records and conversation members with `cli: "antigravity"` keep hydrating and rendering, since `cli` is display data. They age out under the existing retention rules.
- **RTDB leftovers.** `widget/antigravity_status` is deleted once, by hand, during the deploy. It is a one-time migration, so it gets no permanent code. `spawn_options/antigravity` goes with the first startup publish.

## Outside the repo (Windows and WSL)

Done as the first task, so agy's config never names a deleted script. On each machine, read `~/.gemini/config/hooks.json` and `~/.gemini/config/mcp_config.json`, back both up to the session scratch directory, remove only their `switchboard` entries, then remove the installed `switchboard` agy plugin with agy's own uninstall command. agy and its other plugins stay. Confirm afterwards that `agy plugin list` no longer shows `switchboard`.

## Docs and tracking

- **Updated:** `docs/switchboard-design-spec-comprehensive.md`; `README.md` (its Antigravity CLI subsection); `CLAUDE.md` (the Layout entries for `antigravity_status.py`, `agy-identity-hook.py` and the marker sweep, Setup step 3, the Hooks section's agy paragraph and marker mentions, and the MCP section's non-Claude note); `watchtower/README.md`.
- **Unchanged, as history:** the dated specs under `docs/superpowers/specs/`, `PROJECT-JOURNAL.md`, and `.graphify-verdicts.json` (the src-view rebuild reports verdicts for deleted code as stale and keeps them).
- **Backlog:** cut T-283 and T-261. Remove T-277's marker-count bullet. Refresh T-249's premise: the mod's `/session_end` replaced the marker, so any hard-killed session now leaves its member alive, and the agy branch it cites is gone. Trim the agy asides in T-252, T-260 and any other entry that still names agy.
- **Journal:** a PROJECT-JOURNAL entry. **Memory:** retire `project_agy_spawn_support`.

## Testing

- **Deleted:** `tests/test_agy_identity_hook.py`, `tests/test_hook_common.py`, `tests/test_turn_end_hook.py`, `tests/test_session_end_markers.py`, `tests/test_antigravity_status.py`, and `AntigravityStatusViewTests.cs`. Mixed test files lose their agy cases.
- **Added (server):**
  - A fresh spawn with `agent: "antigravity"` is rejected with a phone notice and leaves away mode, the session registry and the launcher untouched.
  - A resume of a `cli: "antigravity"` record is rejected the same way.
  - A hydrated `cli: "antigravity"` session record still loads.
  - `/away-mode?session_id=<sid>` returns the flag and leaves that session's notices queued.
  - `/agent_status` pops no notices, whatever the `cli`.
  - The built catalog has no `antigravity` key.
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
