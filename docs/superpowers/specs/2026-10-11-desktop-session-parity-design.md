# Desktop sessions as first-class sessions: the plugin copy the app runs, the inbox poll, and a stale-mod alarm (design)

**Date:** 2026-10-11
**Status:** design approved by John in conversation 2026-10-11, section by section; written spec awaiting his review.
**Backlog:** T-288 (this work). T-289 (an option, not part of this work): refresh the desktop copy automatically from git hooks.
**Evidence:** the investigation of 2026-10-11 in a Claude for Windows desktop session (below), with that session's `/debug` log.

## Goal

Sessions in the Claude for Windows desktop app (the Code tab) never reported context, model, effort or title to Switchboard, went `lost` while idle, and could not receive phone messages or stops until a turn ended. John wants them to behave exactly like terminal sessions, and wants a change to the mod to reach them through one deploy command, without copying secrets.

1. **Package the plugin in its own folder** so the copy the app runs holds only plugin files.
2. **Start the inbox poll in desktop sessions**, giving them idle delivery, stops and the poll heartbeat.
3. **A deploy script** that refreshes the app's copy and proves it matches the repo.
4. **A stale-mod alarm** so a session running an old mod is visible instead of silent.

## Facts the design rests on

**Why desktop sessions never reported (verified 2026-10-11, desktop app 2.31226, Claude Code 2.1.295):**

- The app does not let Claude Code resolve plugins itself. It reads `~/.claude/plugins/installed_plugins.json` and passes each plugin as `{type: "local", path: installPath}` in the `initialize` request it writes to `claude.exe` over stdin (`--await-initialize`). The CLI loads it as `switchboard@inline`; a terminal session loads `switchboard@switchboard` from the directory marketplace at `C:\Work\switchboard`, read live. The app's code is in its bundle; the `@inline` id is in the desktop session's debug log.
- switchboard's `installPath` was `cache/switchboard/switchboard/2.0.0`, copied on 2026-10-03 at commit `f0b5dc6`. That copy registers six events (`session.start`, `prompt.submit`, `tool.call`, `turn.start`, `turn.complete`, `session.end`) and lacks `session.measure`, `turn.step` and `command.run`, so status posts worked and telemetry never did. The debug log showed five model requests and no `turn.step` dispatch to `switchboard@inline`. Every access log since the telemetry change shows zero telemetry posts from the four desktop sessions and dozens from each terminal session.
- `claude plugin update` on the directory-sourced plugin at an unchanged version is a no-op ("read from its folder: nothing to update"). When the version differs, it copies the source into `cache/<version>` and moves `installPath`. The copy is the whole folder except `.git`: `.env`, `serviceAccountKey.json`, `.venv`, `logs` and build output. 2.0.0 is 1.9 GB and 2.1.0 is 1.6 GB; every copy back to 1.6.4 holds `serviceAccountKey.json`.
- Five probes running the app's `claude.exe` with its exact flags, the `initialize` handshake, held-open stdin over two turns and a `reload_plugins` request all posted telemetry, because none passed the cache path as a plugin, so the CLI fell back to the live repo.
- The app watches `installed_plugins.json` and may send running sessions `reload_plugins`. A session mid-turn did not reload when the file changed.

**Immediate fix already applied (2026-10-11):** John bumped `.claude-plugin/plugin.json` to `2.1.0` and committed it; `claude plugin update` created `cache/switchboard/switchboard/2.1.0` with `hooks/` identical to the repo and moved `installPath` there. A desktop session started after that should report telemetry; not yet confirmed in one.

**Why desktop sessions cannot poll today:**

- The engine raises `session.start` with `isInteractive: false` and `surface: null` for a desktop session, the same as for a `-p` run (seen in the probes' debug logs: "session.start: raised (surface none, not interactive)").
- The mod starts its inbox poller only when `isInteractive` (`hooks/switchboard.ts`, the `session.start` hook). A `-p` run is gated out on purpose: it could pop a queued notice and exit before delivering it.
- A session that never polls is judged by silence: lost after `session_lost_after_seconds` (900 s) without an event (`server/session_registry.py`, the sweep). That is why idle desktop sessions show `lost` with recent activity.

**Engine API (claude-code.d.ts shipped with 2.1.295):**

- `$.session.surfaces()` returns every surface the session draws on; empty in a plain `-p` run.
- `session.attach` fires when "a surface joined the session's roster of attached clients (a phone opened the session; the desktop app connected)", with `surface` and `clientId`. The terminal's own binding raises nothing.
- `$.plugin.root` is the plugin's directory (the one holding `plugin.json`), absolute. `$.fs.read` refuses files over 4 MiB.

**Plugin docs (via the Claude Code docs agent; probe P1 confirms before relying on them):**

- Version precedence for a relative-path plugin: `plugin.json` `version`, then the marketplace entry's `version`, then a source-type fallback: the commit SHA of the installed directory inside a git marketplace, `unknown` outside one. `update` re-copies only when the computed version changes. The update code forces an overwrite when the version is `unknown`.
- A marketplace entry's `source` may name a subfolder (`"./plugin"`). Only that folder is copied, and component paths may not point outside it.

## Decisions

1. **Full parity with terminal sessions.** Rejected: polling for liveness only (phone messages wait for John's next prompt), and telemetry only (desktop sessions keep going `lost`).
2. **The plugin moves into `plugin/`; `skills/` moves with it.** agy's root manifests (`plugin.json`, `hooks.json`, `mcp_config.json`) stay; an agy install loses the switchboard skill and keeps its hooks and MCP config. Removing agy remains its own backlog item.
3. **`plugin.json` drops its `version`**, so each commit is a new version and `update` copies without a manual bump (subject to probe P1).
4. **The deploy is a manual script.** Running it from git hooks is backlogged as T-289.
5. **The poll starts on a surface**, not on an environment variable: the documented `session.attach` signal, with `$.session.surfaces()` for a hot reload. `CLAUDE_CODE_ENTRYPOINT === "claude-desktop"` is the fallback if probe P4 finds no attach.
6. **A stale-mod alarm is included**, keyed on the mod's content, not its version, so a commit that does not touch `plugin/` never flags anything.
7. **The draft-in-prompt-box deferral is not reproduced for desktop sessions.** The engine cannot see the app's input box, so a phone message to an idle desktop session is delivered even while John is typing there.
8. **No pruning of old copies.** With the subfolder, a copy is a few hundred KB. Deleting the old 1.6 to 1.9 GB copies is a one-time manual step for John (2.0.0 only after the desktop sessions that loaded it have restarted).

## Part 1: packaging

- Move into `plugin/`: `.claude-plugin/plugin.json` (to `plugin/.claude-plugin/plugin.json`, without `version`), `hooks/` (the mod, `hooks.json`, `tests/`), `skills/`, `types/`.
- `.claude-plugin/marketplace.json` stays at the root; its entry's `source` becomes `"./plugin"`.
- Update every path that names the old layout: `tests/test_mod_plugin.py` (`validate` on `plugin/.claude-plugin/plugin.json`, `test` on `plugin/`), `scripts/mod-typecheck.sh` (`plugin/hooks`, `plugin/types`), `.graphifyignore` and `.graphify-verdicts.json` entries that name `hooks/` or `types/` paths, and CLAUDE.md (layout, Hooks, Setup). Find the rest by grep for `hooks/`, `skills/` and `types/` in tracked files; `scripts/` Python hooks for agy stay where they are.
- The move is one commit. Before it, probe P2 decides how running sessions take it; after it, John relaunches terminal sessions (Windows, and WSL after its `git pull --ff-only`), then runs the deploy script.
- After the move, check that a new terminal session loads the mod from `plugin\hooks\hooks.json` (debug log "Read hooks.json for plugin switchboard"), on Windows and in WSL. If a directory marketplace caches the old `source`, refresh it with `claude plugin marketplace update switchboard` and record that in CLAUDE.md.

## Part 2: the inbox poll in desktop sessions

- **Gate.** The poller starts when any of these holds, and at most once per module load (a module-level flag; a second start never adds a second timer):
  - at `session.start`, `e.isInteractive` (terminals, as today);
  - at `session.start`, `(await $.session.surfaces()).length > 0` (a desktop session whose mod hot-reloaded after the app attached: `session.start` fires again then, `session.attach` does not);
  - on `session.attach`, for any surface.

  A `-p` run has no surfaces and never attaches, so it still never polls.
- **Behaviour.** Unchanged code paths: idle, a held phone message is submitted with `$.prompt.submit` and starts a turn; busy, messages ride the next main-loop tool result, a typed prompt or the turn-end prompt; a stop calls `$.turn.abort`; each poll is the liveness heartbeat. The server needs no change: `note_poll` already moves a session onto the 90 s poll rule.
- **Where it fails.** If probe P5 or P6 shows the app does not display a mod-started turn or does not stop on `$.turn.abort`, stop and bring it back to John before shipping Part 2: full parity depends on both.

## Part 3: the deploy script

`scripts/deploy-mod.ps1`, run by hand after a commit that touches `plugin/`:

1. Find the marketplace source from `~/.claude/plugins/known_marketplaces.json` (`switchboard.source.path`), not a hard-coded path.
2. Refuse, exit 1, if `git -C <source> status --porcelain -- plugin` is not empty: the version is the commit, so `update` would not copy uncommitted edits.
3. Run `claude plugin update switchboard@switchboard`; exit 1 if it fails.
4. Read switchboard's `installPath` from `~/.claude/plugins/installed_plugins.json`, compare every file under it with `<source>/plugin` by SHA-256, and exit 1 listing missing, extra and different files if they do not match.
5. Print: new desktop sessions load this copy; running ones keep their mod until restarted, and `/healthz` lists them.

Windows only: WSL has no desktop app, and its terminal sessions read its clone live.

## Part 4: the stale-mod alarm

- **Fingerprint.** SHA-256, lowercase hex, over the top-level `*.ts` files of the plugin's `hooks/` folder (not `hooks/tests/`), in file-name order, framed like `server/build_info.py`'s `source_fingerprint`: for each file, the UTF-8 bytes of `"{name}\n{length}\n"`, then its bytes with CRLF replaced by LF (`length` counts the replaced bytes). Revised at planning: the spec first had a NUL-separated framing; reusing the existing one keeps a single scheme in the repo. Test vector, pinned in both the mod's tests and pytest: `a.ts` = `one\r\ntwo\n` and `b.ts` = `three` give `32495c7f04ecccc8c3dddc24cf463335502ae7eb098a8cffce64442bb67c06b5`.
- **Mod.** Once per session id (so again after a `/clear`), it lists and reads those files under `$.plugin.root` as bytes (so the digest matches the server's whatever the encoding), computes the fingerprint with `crypto.subtle.digest` and sends `{ "mod_fingerprint": ... }` as a telemetry report of its own. Revised at planning: the spec first added it to the session's first report, but existing tests pin exact report bodies and a separate report leaves them valid. If listing, reading or hashing fails it sends none and writes a debug line.
- **Server.**
  - `server/telemetry.py` validates `mod_fingerprint` (64 lowercase hex) field by field like the rest; a bad value is dropped.
  - `SessionRecord` gains `mod_fingerprint` and `mod_stale`: null when the session never reported one (agy, or a mod older than this change), else whether it differs from the repo's.
  - The repo's fingerprint is computed from `plugin/hooks` under the repo root at startup and again each sweep tick, so a mod edit without a server restart re-judges every session; a changed `mod_stale` is mirrored to RTDB `sessions/` like other fields.
  - `/healthz` gains `mod: {repo_fingerprint, stale_sessions: [cli_session_id, ...]}` over sessions not ended.
- **Operator.** A session row whose record has `mod_stale === true` shows an "old mod" chip, titled "Restart this session to load the current Switchboard mod". The phone, Watchtower and `/stats` are unchanged.
- WSL sessions show stale while the WSL clone lags `develop`; that is the same drift and wants the same fix (pull and relaunch).

## Probes (before the parts they decide)

| # | Question | Where | Decides |
|---|---|---|---|
| P1 | With no `version` in `plugin.json`, what version does `update` record for a subfolder source in a git repo, and does it copy only the subfolder? | A throwaway directory marketplace that is a git repo, installed under a throwaway name, removed afterwards | Decision 3. If the version is `unknown`, `update` overwrites `cache/unknown` in place every run, which also works; if `update` still refuses, keep a `version` and have the deploy script require John to bump it |
| P2 | What does a running session do when its hooks module's path disappears (an error, a silent unload, or nothing until restart)? | The throwaway marketplace, with a running `-p --input-format stream-json` session | How Part 1's commit is sequenced against John's relaunches |
| P3 | Does the mod's worker have `crypto.subtle.digest`? | `claude plugin test` and the throwaway plugin in a live session | The hash in Part 4; fallback is a small pure-TypeScript SHA-256 |
| P4 | Does the desktop app raise `session.attach` with `surface: "desktop"`, and what does `$.session.surfaces()` return after it? | A fresh desktop session running the Part 2 mod, with `/debug` on | Decision 5 |
| P5 | Does a turn the mod starts with `$.prompt.submit` in an idle desktop session show in the app, output included? | Same | Part 2 ships or comes back to John |
| P6 | Does `$.turn.abort` stop a running turn in a desktop session? | Same | Same |

Revised at planning:
- P3's test-kit half was answered at planning (2026-10-11): `crypto.subtle.digest` SHA-256 and `Uint8Array.fromBase64` work under `claude plugin test`, and a mod's own `$.plugin.root` there is the real plugin folder as a native Windows path. The plugin-authoring declarations type `crypto.subtle`, `TextEncoder`, `btoa` and `atob` for mods. The live half runs with P4.
- P4 to P6 run before any Part 2 code, on a small throwaway probe plugin installed in a throwaway marketplace and loaded by a fresh desktop session, with evidence sent to the server's access log as 404 paths. The spec first ran them after deploying the Part 2 mod, which would have built on an unverified premise.

## Testing and live verification

- **Mod (`claude plugin test`):** each gate rule (interactive start; start with surfaces present; attach; a second start adds no timer; a start with no surfaces and no attach never polls); the fingerprint (sent once per session id, again for a new id, matches the test vector, absent on a read failure).
- **Server (pytest):** `mod_fingerprint` validation; the record fields and their mirror; `mod_stale` true, false and null; the sweep re-judging after the repo fingerprint changes; the repo fingerprint against the shared vector; the `/healthz` shape.
- **Operator (`node --test`):** the chip derivation.
- **Existing suites** pass at the new paths: `tests/test_mod_plugin.py`, `bash scripts/mod-typecheck.sh`.
- **Live, after deploy, in a fresh desktop session:** attach seen in its debug log; model, effort, context and title on the roster; a phone message to it while idle starts a turn shown in the app; a phone stop ends a running turn; idle for more than two minutes it stays not `lost`. For the alarm: an uncommitted comment added to a file in `plugin/hooks` in the main checkout lists the desktop sessions in `/healthz` `mod.stale_sessions` within one sweep tick (terminal sessions re-report after their hot reload and stay current), and reverting it clears the list. Revised at planning: the spec first expected a session started before the deploy to show as stale, but a mod older than this change reports no fingerprint, so it shows null.
- **Live, packaging:** the new copy under `installPath` holds only `plugin/` files (no `.env`, no `serviceAccountKey.json`) and is under 1 MB.

## Rollout order

Revised at planning (all code lands on this branch first, so the relaunches and the deploy happen at the merge):

1. Probes P1 and P2 (throwaway marketplace), then P3 to P6 (throwaway probe plugin in a fresh desktop session).
2. Part 1, Part 3, Part 2 and Part 4 (mod, server, Operator), one commit per plan task; then docs.
3. John merges into `develop`; terminal sessions relaunch (WSL after its pull); the service restarts; the deploy script runs; packaging and desktop live checks.
4. Tracking.

## Docs and tracking on land

- CLAUDE.md: the layout (`plugin/`), the Hooks section (desktop sessions load the cached copy as `switchboard@inline`; the poll gate), Setup, and a deploy note: after a commit touching `plugin/`, run `scripts/deploy-mod.ps1`; WSL pulls as before.
- The comprehensive spec: the mod's poll gate, the fingerprint and `mod_stale`, the `/healthz` `mod` block.
- PROJECT-JOURNAL entry for T-288; T-288's backlog item, if one is added, is cut on land.
- Backlog entry T-289, under "Claude Code mod" in `docs/tracking/backlog.md` (disk-only; this session's worktree cannot write it, so John or a main-checkout session pastes it):

  > ### Refresh the desktop app's mod copy from git hooks
  > <!-- id: T-289 | status: open | surfaced: 2026-10-11 | priority: low | tags: mod,deploy,desktop -->
  >
  > **Surfaced 2026-10-11** while designing T-288. Desktop sessions run the plugin copy under `installed_plugins.json`'s `installPath`, refreshed only by `scripts/deploy-mod.ps1`; forgetting it leaves desktop sessions on an old mod (the stale-mod alarm shows them). Option: have the main checkout's `post-commit` and `post-merge` hooks run the script when a commit touches `plugin/` (merging a worktree branch fires `post-merge`, not `post-commit`). Costs a few seconds on such commits, and `graphify hook install` rewrites those hook files, so the install must be re-runnable. John chose the manual script for now.

## Out of scope

- Removing agy (its own backlog item).
- Running the deploy from git hooks (T-289).
- Showing a stale mod on the phone, Watchtower or `/stats`.
- Holding phone messages while John types in the desktop app's input box.
- Deleting the old cache copies (John, by hand).
- VS Code and other surfaces beyond what the attach gate gives for free.

## Risks

- **The move under running sessions.** Every terminal session loaded its mod from `hooks/`; moving the files may break its hot reload. P2 says how; John relaunches after the move either way.
- **The WSL clone.** Its sessions keep the old layout until a pull and relaunch; the alarm shows them as stale meanwhile.
- **Early-access API.** `session.attach`, `$.session.surfaces()` and `$.plugin.root` may change in any Claude Code update; the mod tests pin the gate, and `tests/test_mod_plugin.py` runs them in pytest.
- **Parity rests on P5 and P6.** If the app does not show a mod-started turn, or ignores `$.turn.abort`, Part 2 does not ship as designed.
- **A version per commit.** If the version is the repo's commit, every commit makes a new copy on the next deploy even when `plugin/` did not change; copies are small and the alarm keys on content, so nothing is falsely flagged.
