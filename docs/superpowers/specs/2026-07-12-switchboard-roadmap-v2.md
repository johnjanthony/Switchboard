# Switchboard Roadmap v2 — The Hardening Quarter

**Date:** 2026-07-14 (brainstormed; filename carries the planned session date)
**Status:** Approved by John (skeleton, ordering, and every per-axis disposition decided in AskUserQuestion rounds, 2026-07-14)
**Predecessors:** `2026-07-06-convening-chunks-roadmap.md` (convening program, COMPLETE 2026-07-08); the 2026-07 technical-review remediation (WP-1..WP-10) and the dead-code/DRY sweep, all landed by `dd281f9` (2026-07-12). This roadmap is their successor and the sequencing authority for the next 2-3 months.
**Progress (2026-07-14):** chunk 1 (live-smoke harness) EXECUTED + live-verified 2026-07-14 — READY TO MERGE, uncommitted (record the hash here on John's commit; see the chunk-1 as-built note). Chunk 2 (away-mode integrity) is next; its plan is written after chunk 1 commits.
**Plans:** per the established pipeline, each chunk's implementation plan is written only after the preceding chunk lands (plans are grounded in exact signatures; specs are stable against interfaces). Plans live in `docs/superpowers/plans/` (gitignored working docs); decisions and as-built deltas fold back into this file.

## Ranking spine (decision)

**Hardening-first.** The 2026-07 review wave hardened code quality (10 WPs, 26 REV findings, a dead-code pass). What it did not harden is the product contract: away mode still has a known enforcement hole (T-230, the sole HIGH in the backlog), delivery still has confirmed lose-data races, deploy verification is still a manual smoke with John at the desk, and the installer still lies about success. This quarter makes the contract trustworthy. New capability (multi-machine, voice, analytics, human-to-agent injection) is deliberately parked — see Not doing.

Grounding: four read-only Opus explorers ran against the tree 2026-07-14 before any decision. Load-bearing findings: T-006 (ask_human rate limiting) is already shipped and the backlog entry is stale (`handlers.py:265` consumes the bucket); T-141's Bearer gate satisfies the "not usable from the LAN" acceptance reading, with residue = installer-enforced firewall scoping + token provisioning; T-174/T-193/T-029/T-181/T-185/T-189/T-191 claims all reconfirmed against current code; T-001's only genuinely unfinished slice is `message_and_await_agent` wait survival (ask_human already hit its ceiling with parked pendings, and reviving live tool calls is impossible while `stateless_http=False` severs every MCP session on restart).

## Ordering

1. **Live-smoke harness** — built first (John's call, over shipping the HIGH first) so every subsequent chunk including the away-mode chunk deploy-verifies against it.
2. **Away-mode integrity** — T-230 + T-174 + T-193, one plugin bump.
3. **Server delivery races** — T-181, T-185, T-192, T-189.
4. **Android delivery** — T-204, T-200, T-206 residual, T-024, T-157, T-208 rider.
5. **Ops/install hardening** — T-141 residue + T-196 + T-197.
6. **Watchtower batch** — T-162, T-231, T-214.
7. **Operator polish** — T-220, T-221.
8. **Cloud accumulators** — T-007 narrow (admin_notifications + Storage blobs).

Capacity call: 8 chunks in 2-3 months approved as-is (chunks 7-8 are small enough to batch into gap days). DRY cluster T-234..T-238 gets NO dedicated chunk — each subsystem chunk folds its own DRY item opportunistically. Fold map: T-238(b) hook boilerplate → chunk 2 (the quarter's only plugin bump); T-235 → chunk 4; T-236 → chunk 6; T-234 → chunk 7; T-237 (server dispatch-loop skeleton) and T-238(a) (spawn-launcher legacy branch) have no natural home this quarter and stay backlogged unless a chunk's plan happens to touch those files. Anything unfolded by quarter's end stays backlogged.

## Chunk 1 — Live-smoke harness

**Scope:** a repeatable post-deploy smoke an agent can drive end-to-end against the real service and real Firebase: away-mode toggle round-trip, `ask_human` → RTDB answer write → resolution observed, restart survival (parked-pending rebuild + `/healthz` parked count), `/healthz` listener/loop assertions. The WP-10 manual smoke, scripted. Honors Live Over Mock: the suite's 839 mocked tests prove the model, only this proves the system.

**Rationale:** every hardening chunk ahead regresses in exactly the places only a live run shows (hooks, restart, FCM, listener supervision). Automating the smoke makes chunks 2-8 cheaper and less John-dependent, and it compounds: each chunk extends the harness with its own assertion (chunk 2 adds an AskUserQuestion-guard trip check, chunk 3 adds a fast-answer race probe, etc.).

**Rough decomposition:** (1) harness skeleton + config (service-account driven RTDB writer to play John's side; loopback HTTP; explicit test-conversation naming); (2) away-mode round-trip + ask/answer/resolve flows; (3) restart-survival flow (drives `restart-service.ps1 -SkipTests`, waits for `/healthz`, asserts parked rebuild); (4) cleanup semantics (test conversations force-ended + eligible for the retention sweep, or explicitly deleted); (5) runner ergonomics (single entry point, clear PASS/FAIL report, safe-to-rerun).

**Design owed:** the full design pass. Open questions: who plays the asker (a raw MCP client in the harness vs the orchestrating agent's own switchboard MCP session, the WP-10 pattern — the MCP-client option survives restarts, the agent-session option is simpler but dies with the service); where it lives (`scripts/smoke/` vs a pytest `-m live` tier excluded from the default run); how phone-side effects (FCM) are asserted or explicitly out of scope; restart-flow interaction with the orchestrator's own MCP session (the known stateful-HTTP severance).

**Verification:** the harness proves itself against the currently-deployed service before anything else ships. No plugin bump; no client builds.

**As-built (executed 2026-07-14, in-place Opus SDD run, READY TO MERGE):** delivered `scripts/smoke/_smoke_lib.py` + `scripts/smoke/smoke.py` (both LF, per the `scripts/*.py` module convention) + a CLAUDE.md Testing subsection; suite 839 unchanged, nothing under `server/`/`tests/`, no plugin bump. Both live runs passed with John's approval (`--skip-restart` flows 0-3 + cleanup; full run all flows, ~65s). Deviations folded from the plan: (1) the plan defined `load_env`/`init_firebase` but never showed the call site — `main()` now initializes Firebase gated on `--preflight-only` (the gap was invisible to read-only per-task verification; lesson recorded in the pipeline memory); (2) cleanup is not a FLOWS entry — it is the fail-fast exception, run from `main()`'s `finally` (FLOWS = 5); (3) report strings use ASCII hyphens (no-em-dash-in-code rule + cp1252 console safety). **Live-confirmed nuance that corrects this spec's Flow-4 wording:** a blocked `ask_human` does NOT get a prompt transport error when the restart severs its MCP session — it HANGS, and the harness's own bounded wait (15s) fires TimeoutError; the real proof of restart survival is the `pending.parked == 1` assertion, not the exception type. Accepted minor: Flow 3 can orphan its blocking task on its own assertion-failure path — bounded, because cleanup ends the conversation, which terminates the dangling ask server-side.

## Chunk 2 — Away-mode integrity

**Scope:** T-230 (HIGH) + T-174 + T-193. One plugin version bump covers all three (1.4.3 → 1.5.0).

- **T-230 — guard hook + SKILL text (decided).** New PreToolUse hook script: self-filter on `tool_name == "AskUserQuestion"` (the injector pattern), query `GET /away-mode` (reuse `turn-end-hook-away-mode.py`'s `_fetch_state` mechanics: `SWITCHBOARD_BASE_URL`, 0.5s timeout, optional Bearer), and when away is active emit a PreToolUse deny (`hookSpecificOutput.permissionDecision: "deny"`) with a reason steering the agent to re-ask via `ask_human` (translating options into `suggestions`). Fail-open on any error. Plus a two-line SKILL.md prohibition so well-behaved agents never trip the guard — belt-and-suspenders decided over hook-only because both ride the same plugin bump and the failure (silently stranding John) is severe. Grounding confirmed no server-side change is needed: `/away-mode` already returns `{"active", "notices"}` unauthenticated-on-loopback.
- **T-174 — turn-end hook cwd fail-open.** The Stop hook returns 0 (no enforcement) when the payload omits cwd, before ever querying `/away-mode` (`turn-end-hook-away-mode.py:102-103`). Fix: query without requiring cwd; keep fail-open only for genuine errors (connection refused / non-200 / malformed body); demote cwd to a display tag.
- **T-193 — marker-dir fallback lands in the plugin cache.** When `SWITCHBOARD_MARKER_DIR` is unset the SessionEnd marker falls back to `<plugin-cache>/logs/session-end`, which the server never sweeps — sessions silently never go dormant. Fix: hook-side breadcrumb log at minimum, plus a server-side startup warning when the sweep dir looks unused while conversations hold live members. Whether the fallback can resolve to the server's real logs dir is a design question (the hook does not know the server's location).

**Optional fold:** T-238(b) hook-script boilerplate DRY (shared base-URL/token/stdin helper) — this is the only chunk this quarter paying a plugin bump, so it is this fold's only cheap window. Decide at chunk design time whether the added churn to three enforcement-critical scripts is worth it in the same chunk that modifies them anyway.

**Design owed:** deny-reason wording (agents act on the literal text — the reason must carry the ask_human re-ask instruction and the suggestions translation guidance); whether the guard also covers other in-terminal blocking tools or stays AskUserQuestion-only; T-193 fallback-target decision; T-238(b) fold go/no-go.

**Verification:** harness (chunk 1) + a live AskUserQuestion trip under away mode with the bumped plugin active (the plugin-cache staleness gotcha applies — verify against `installed_plugins.json`, and the WSL marketplace clone needs its own pull + update).

## Chunk 3 — Server delivery races

**Scope:** the confirmed lose-data / stranded-state class. T-181 (pending registered only after the full Firebase write + FCM send — a fast answer lands in the unknown-correlation branch and is discarded while the asker blocks 24h; fix: register the pending before or atomically with the message write). T-185 (combine/resume flip members alive and bind sessions before the launcher runs; a failed `schtasks` launch strands a permanently alive-with-no-process member whose advertised phone recovery is dead — fix: flip alive/bind only after launch confirmation, or roll back to dormant on failure). T-192 (an error_logger failure propagates out of `record_crash` and kills the supervisor/dispatch loop it was protecting — wrap the logging awaits). T-189 (sender validation blocks only `"__"` while sender interpolates into RTDB paths; reject Firebase-illegal key characters at `_validate_sender` and at member-creation sites).

**Design owed:** T-181's ordering redesign must be reconciled with the REV-002 away-exit re-check window that currently sits between `registry.add` and the parked-record write (`handlers.py:303-322`) — the design pass decides what "atomically" means among five sequential awaits plus a fire-and-forget. T-185 rollback semantics (roll back vs confirm-then-flip) and its interaction with the accepted T-142 TOCTOU (stays parked; this fix must not widen it).

**Ride-alongs:** fix the stale CLAUDE.md rate-limiter line and the design-spec rate-limit list (grounding: all four tools consume the per-conversation bucket, including `message_and_await_agent` via REV-109's suppress-push semantics — both docs under-report). No plugin bump.

## Chunk 4 — Android delivery

**Scope:** the phone-side delivery defects. T-204 (notification IDs restart at 1 after process death — a new question replaces a still-unread one in the tray, losing a pending question's only visible signal in away mode; fix: hash-derived IDs). T-200 (unread zeroed while the app is backgrounded on a conversation — gate the zero-write on foreground lifecycle). T-206 residual (VM writes attach no completion listeners, so permission-denied writes fail silently — attach and surface rejections; the sign-in half already shipped in WP-6). T-024 (malformed `suggestions` silently leak into the question body — server-side MCP-boundary validation rejecting non-array-of-strings shapes, plus a SKILL/tool-description shape example; server work riding a client chunk because the symptom is phone-side). T-157 (malformed String message nodes — investigate the producer; may close as legacy data). **T-208 rider (design-tier addition, flagged for John's review):** the Android markdown LinkResolver has no scheme allowlist (Operator parity gap, confirmed at `MainActivity.kt:518-524`); the fix is a ~5-line allowlist in a file this chunk already touches.

**Fold:** T-235 — app/wear `SwitchboardApplication` + FCM-service duplication hoisted to `shared/`, and the never-read `ConversationMember`/`RegistrySession.cli` field decision (delete the client-side parse vs keep as deliberate node mirror — John decides in the chunk design).

**Design owed:** T-157 disposition (depends on what the producer hunt finds); T-235 node-mirror decision; T-024 validation error shape (actionable error to the calling agent, not a silent drop).

**Verification:** android unit suites + compile gates (never bare `gradlew build`); emulator smoke for T-204/T-200; no plugin bump (client + server validation only).

## Chunk 5 — Ops/install hardening

**Scope:** close T-141 for real, plus the install-script correctness pair. T-141 residue (grounding verdict: the WP-5 Bearer gate satisfies the "not usable" acceptance; what never shipped is reachability + provisioning): the installer creates the WSL-subnet-scoped inbound firewall rule instead of printing a warning, and provisions/validates `SWITCHBOARD_TOKEN` for non-loopback binds. T-196 (default service account is malformed `.JohnAnthony` — no backslash — so the service silently runs as SYSTEM in Session 0, the exact environment where spawn misbehaves). T-197 (no nssm/native exit-code checks — `restart-service.ps1` prints success while the service is down; add `$LASTEXITCODE` checks + a `SERVICE_RUNNING` poll).

**Design owed:** the firewall rule's shape under both WSL networking topologies (the 2026-06-11 doc rated installer-enforced scoping "brittle" — the design pass must resolve NAT vs bridged subnet discovery, and what happens when the WSL subnet changes across reboots). Token rotation explicitly out of scope (single-user threat model; plain env-var compare stays). Chunk-1 live finding to carry into any restart reasoning here (T-197): a blocked MCP call over the stateful HTTP transport HANGS across a service restart rather than erroring promptly — designs must not assume severed sessions announce themselves.

**On completion:** propose T-141 → ledger (satisfied by WP-5 option (a) + this chunk's option (b/d) residue), citing both commits.

## Chunk 6 — Watchtower batch

**Scope:** T-162 (persist a small last-sessions/last-stats snapshot under `%APPDATA%` on each UI update; load and render it on startup before the first live poll — John's standing ask). T-231 (widget invisible after explorer restart: re-embed succeeds but the layered child never repaints — needs a Win32 investigation spike into `WS_VISIBLE`/z-order/`UpdateLayeredWindow` re-push immediately post-`SetParent`). T-214 (quota auth failure spawns up to two quota-consuming `claude -p` turns per poll indefinitely — back off after auth failure until credentials change).

**Fold:** T-236 — severity-color constants centralized, transcript-scan loop extracted (exception-handling differs per copy; verify), `Lerp`/rounded-rect dedup; the test-only members decision (keep-as-API vs inline) rides the same chunk.

**Design owed:** T-231 root cause is unknown — the chunk design should budget the investigation spike explicitly and accept that the fix lands behind it; T-162 snapshot format/location (cache file schema).

**Verification:** `dotnet test` from `watchtower/`; the Release artifact is the published single-file exe (tray-quit before rebuild); manual smoke required for T-231 (explorer restart) — the live harness does not cover Win32 surfaces.

## Chunk 7 — Operator polish

**Scope:** T-220 (admin notifications render raw — run through the markdown renderer + relative-time the ISO timestamp). T-221 (the `/stats` healthy predicate is hand-copied between server and dashboard and can transiently disagree with the Watchtower widget — have the dashboard consume `/stats.healthy` or extract one predicate).

**Fold:** T-234 — the triple-copy HTML escaper consolidated (XSS-relevant; consolidate carefully), the three drifting age/duration formatters unified, and the orphaned `rightCollapsed` store surface wired-or-removed.

**Design owed:** minimal — T-221 mechanism choice; T-234 `rightCollapsed` wire-or-remove call.

**Verification:** `node --test dashboard/*.test.js`; zero-build deploy (browser refresh); the Preact component layer's correctness gate remains reviewer diff-tracing (no node harness for components).

## Chunk 8 — Cloud accumulators

**Scope:** the two unbounded cloud-side accumulators (John's call: cloud only; logs and in-process micro-leaks stay parked). (1) `admin_notifications` retention — written push-only with no delete anywhere; add an age-based prune riding the existing hourly sweep. (2) Storage blob cleanup — `send_document_human` blobs live forever (only the signed URL expires, 7 days); when the retention sweep deletes a conversation's nodes it must also delete the conversation's document blobs. Narrows T-007; in-flight message pruning for long-lived active conversations is explicitly NOT included.

**Design owed:** blob-path discovery at sweep time — the `storage_path` lives in the `messages/<conv_id>` node the same multi-location update deletes, so the sweep must read paths before deleting (or an index must exist); `admin_notifications` retention window choice.

**Verification:** harness extension (sweep assertions) + a live sweep observation against real RTDB/Storage.

## Not doing (explicit)

Each entry stays on the backlog with its documented trigger; this list is the quarter's deliberate scope fence, not a rejection.

- **T-025 multi-machine A2A + T-026 residue** — parked (John, 2026-07-14). Trigger unchanged: a second workstation actually materializes. T-026's remaining display-path wiring decision parks with it.
- **T-001 live-future half** — parked at current state (John, 2026-07-14). Grounding scoped the residual precisely: `message_and_await_agent` wait_queue survival via the parked pattern (write-behind + hydration rebuild + parked-wait wake arms + notice delivery). ask_human is at its accepted ceiling (parked pendings). The severed-MCP-transport limitation is formally accepted — reviving live tool calls across restart is out of reach while `stateless_http=False` (T-032's territory).
- **T-032 MCP session resumption** — upstream/physics; tied to Anthropic #27142. Unchanged.
- **T-029 away-mode persistence across restarts + T-019 mid-turn leak scan** — not selected for the away program. The startup `reset_all_away_mode()` behavior stays; T-029's trigger (Anthropic fixes #27142, or persistence UX pain) stands.
- **T-164 human-to-agent injection, T-165 analytics panel** — the two Operator capability investments; not this quarter.
- **T-009 notification action buttons** — parked (John, 2026-07-14): opening the app is acceptable; chips work there.
- **Feature tail** — T-022 snooze, T-013/T-014 voice+Auto, T-015 presence heartbeat, T-017 inbound documents, T-018 pause button, T-004 non-blocking messaging: all keep their triggers; none scheduled.
- **Janitorial remainder** — T-005/T-216 log rotation, T-191/T-171 in-process micro-leaks, T-163 hidden-active ageout residue: parked at single-user scale.
- **Low background races and residue** — T-142, T-143, T-168, T-170, T-172, T-033, T-034, T-175/T-203 residues, T-002, T-003, T-011, T-020, T-138, T-194, T-195, T-198, T-205, T-209, T-212, T-217, T-237, T-238(a): stay backlogged; several have explicit triggers. T-195 (declare starlette/anyio) is a two-line `pyproject` fix any chunk may fold opportunistically; T-205 (deep-link extra re-fires) and T-020 (SKILL polish) are natural fold candidates for chunks 4 and 2 respectively if their plans have room.

## Tracking proposals (John applies; this roadmap does not edit the tracking docs)

- **Close T-006 as obviated** — ask_human consumes the per-conversation bucket today (`handlers.py:265`, shipped with the M10-era remediation); the backlog text and CLAUDE.md line 33 are both stale. Ledger row + cut.
- **T-030 narrows** to its parameter-tuning question only (limits exist on all four tools now); annotate rather than close.
- **Doc-accuracy ride-alongs** (chunk 3): CLAUDE.md rate-limiter line; design-spec §14/§15.3 rate-limit tool list.
- **Scheduled items** keep their current priorities; on each chunk's land, cut → ledger per the §8 ritual, and re-check siblings the ship obviates (the reconciliation discipline).
- **Stale line anchors** noted by grounding (T-189 now `handlers.py:165-168`; T-208 now `MainActivity.kt:518-524`) — fold corrections when those sections are next edited.

## Method (unchanged, restated)

The proven pipeline applies per chunk: Fable design tier (brainstorm → spec-or-roadmap-section → grounded plan + kickoff prompt) → fresh Opus SDD execution session (2-for-2 so far; zero-escalation last run), sonnet implementers / opus reviewers, snapshot.sh no-commit diffs, John commits at gates. Suite gates per stack: pytest with explicit `--basetemp`; `node --test dashboard/*.test.js`; `dotnet test` from `watchtower/`; android `:shared:testDebugUnitTest` + compile gates (never bare `gradlew build`). Plugin-facing chunks (chunk 2 only, this quarter) bump `.claude-plugin/plugin.json` and re-propagate to the WSL marketplace clone. This file's Progress header + per-chunk as-built folds are the status of record.

## First chunk recommendation + handoff

**Next action:** design pass for chunk 1 (live-smoke harness). Its open questions are enumerated in the chunk section above; the highest-leverage decision is who plays the asker (raw MCP client vs orchestrator-session), because it determines whether the restart-survival flow can be asserted in one run.

**Handoff note for the next design-sprint session:** read this file top to bottom (it is the sequencing authority), then `project_fable_week_chunk_pipeline` memory for the method and trap lore. All 2026-07 remediation is landed; the backlog is freshly reconciled (68 open, T-006 close proposed above). Chunk 1 needs a brainstorm + spec (it is new machinery, not a fix batch — roadmap-section-as-spec is NOT sufficient; give it a dated spec doc); chunks 2-8 can mostly run on roadmap-section-as-spec plus a grounded plan each. Ground every plan against the tree at write time (the four 2026-07-14 explorer reports cited here are session artifacts — re-verify anchors before reuse). John applies the tracking proposals; agents never edit `docs/tracking/`.
