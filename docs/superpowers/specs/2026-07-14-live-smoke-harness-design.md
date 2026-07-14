# Live-Smoke Harness — Design (Roadmap v2, Chunk 1)

**Date:** 2026-07-14
**Status:** Approved by John (design presented and approved 2026-07-14; all four mechanism decisions made in AskUserQuestion rounds the same day). EXECUTED 2026-07-14 — see the As-built section at the end; the Flow-4 severance wording in §Flows is corrected there.
**Parent:** `2026-07-12-switchboard-roadmap-v2.md` (chunk 1 — first chunk of the Hardening Quarter). This spec is the chunk's authority; the roadmap section is the summary.
**Plan:** written after this spec is committed, into `docs/superpowers/plans/` (gitignored), grounded against the tree at write time.

## Problem

Every deploy verification today is a manual smoke with John at the desk and phone: toggle away mode, ask a question, answer it from a surface, restart the service, eyeball `/healthz`. The suites (839 pytest / 151 node / 107 android / 163 xUnit) prove the model against mocks; nothing but the manual ritual proves the live system — and the hardening chunks ahead (hooks, restart survival, delivery races) regress in exactly the places only a live run shows. The harness scripts that ritual so an agent can run it end-to-end and read a PASS/FAIL report.

## Decisions (John, 2026-07-14)

1. **Asker = raw MCP client.** The harness opens its own streamable-HTTP MCP session against `http://127.0.0.1:9876/mcp` and calls the tools directly, supplying its own minted `cli_session_id` + `cwd` in the tool arguments (standing in for the injector hook). Chosen over the orchestrator's-own-MCP pattern (severed by the restart flow it needs to assert; not scriptable standalone) and headless `claude -p` (quota, latency, a moving part the smoke isn't testing). The `mcp` package's client half is already importable in the repo venv — zero new dependencies.
2. **Packaging = standalone script**, `scripts/smoke/smoke.py`. Chosen over a pytest `-m live` tier because `restart-service.ps1`'s gate runs pytest while the service is STOPPED — a live test accidentally collected there would fail every restart; a standalone script cannot leak into the suite.
3. **Residue = end + hide, accept pings.** The run's conversation is ended via `leave_conversation`, hidden via a `meta.hidden = true` write (the phone/Operator affordance), and left for the 72h retention sweep. Real FCM pushes fire during a run (1-2 pings on the Questions/Updates channels) — accepted; no server-side quiet mode (test-only branching in the production send path was declined).
4. **Safety = guard + restore; restart flow default-ON.** Abort at start if away mode is already ON (a real away session may be in progress) unless `--force`; always restore the prior away state in cleanup. The service-restart flow runs by default (post-deploy, live MCP sessions are already severed) with `--skip-restart` for at-desk runs while other agents are live.

## Identity and naming

Each run mints `run_id` (8 hex). `sender = "smoke-<run_id>"` (unique per run — the conversation-discovery key), `title = "SMOKE <run_id>"`, `cli_session_id = uuid4()`, `cwd` = the repo root path (display-only, real). The unique sender guarantees discovery never matches a real agent's conversation and makes phone/Operator residue self-identifying.

## Flows

Ordered, fail-fast: a flow failure skips the remaining flows except cleanup, which always attempts. Every assertion failure reports observed-vs-expected detail.

**Flow 0 — preflight.** `/healthz` returns 200 with every listener `live`; snapshot per-listener/loop crash counts (compared again at the end — any increment during the run is a failure). Away-mode guard per Decision 4: if `GET /away-mode` says active, abort unless `--force`; record the prior state for restore. `POST /session_start` with `{session_id, cwd, source: "startup"}` — required, not optional: `session_registry.queue_notice` silently drops notices for unknown session ids (`session_registry.py:278-287`), and Flow 4's notice assertion depends on the record existing. Registers the harness on the roster (transient smoke row; ended in cleanup).

**Flow 1 — away-mode round-trip.** `set_away_mode(true)` via MCP → assert `GET /away-mode` `active: true` AND RTDB `global_settings/away_mode == true`; `set_away_mode(false)` → assert both flip back. Leaves away OFF for Flow 2.

**Flow 2 — at-desk redirect + conversation discovery.** With away OFF, `ask_human` returns the at-desk ERROR sentinel immediately (assert the literal contract string) and auto-mints the run's conversation (`mint_if_unbound`, origin `fallback` — deliberately NOT a ref-less `join_conversation`, whose candidate rule could land the harness in a real agent's still-solo room). The harness then discovers its `conversation_id` by polling the RTDB `conversations/` index for the conv whose `members_active` contains the unique sender. Assert the redirected question landed in `/messages/<conv_id>` as a `notify`-type message.

**Flow 3 — live ask → answer → resolve.** `set_away_mode(true)`. `ask_human(question, suggestions=[...])` in a background task (blocks). Assert: `pending_questions/<request_id>` record appears under the conversation carrying the suggestions list; `/healthz` `pending.count` rises. Write the answer to `/answers/<conv_id>/<request_id>` as `{text, sender, request_id, written_at}` — grounded requirement: `text` and `sender` must both be strings or `_enqueue_answer` drops the write as malformed (`firebase.py:979-995`). Assert: the blocked call returns exactly the answer text; the `human`-type message lands in `/messages/<conv_id>`; the pending record is deleted; `/healthz` `pending.count` falls and `total_answered` increments. Also: `notify_human` returns `"ok"` while away is on.

**Flow 4 — restart survival** (default ON; `--skip-restart` skips). `ask_human` blocks in a background task; assert the pending is visible. Drive `powershell -ExecutionPolicy Bypass -File scripts/restart-service.ps1 -SkipTests` via subprocess, then poll `/healthz` OURSELVES until 200 + all listeners live (bounded, ~60s) — the script's exit code is untrustworthy until T-197 ships (chunk 5). Assert, in order: the blocked MCP call DIED with a transport error (expected — `stateless_http=False` severs sessions; the error is asserted, not tolerated); `GET /away-mode` reports `active: false` (the documented startup `reset_all_away_mode()` contract — restarts force away OFF so pre-restart agents fall back to the terminal; asserting it pins the T-029 status quo); `/healthz` reports `pending.parked == 1` (hydration rebuilt the question future-less); the answer write resolves it — parked drains to 0, the pending record deletes, the `human` message lands in history; `GET /away-mode?session_id=<ours>` pops a notice containing the answered-your-earlier-question text (`finish_parked_resolve` → `queue_notice` → `pop_notices`, the turn-end hook's exact read path — this works post-restart because hydration restores the session roster from the RTDB `sessions/` mirror, so the harness's record exists for `queue_notice`). Answer resolution is away-mode-agnostic by design (away gates question creation, not resolution), so the post-restart away-OFF state does not affect the drain. All subsequent MCP calls use a fresh session.

**Flow 5 — cleanup (always attempted, even on failure; `--keep` skips for debugging).** Fresh MCP session → `leave_conversation(parting)` → assert `meta/state == "ended"`. Write `meta.hidden = true`. Write a SessionEnd marker file into the server's `logs/session-end` dir (same host — the harness knows the repo path; atomic temp + `os.replace`, the hook's pattern) and assert the session record transitions to `ended` via the marker sweep. Restore the prior away state (MCP `set_away_mode`; direct `global_settings/away_mode` write as fallback if the MCP path is unavailable, verified via `GET /away-mode`). Final `/healthz`: crash counts unchanged from the Flow-0 snapshot.

## Mechanics

- **Language/runtime:** Python 3.11+, asyncio, same venv as the server. Entry: `python scripts/smoke/smoke.py` (from the repo root; `.venv\Scripts\python.exe` works identically).
- **MCP client:** `mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`; `initialize()` handshake then `call_tool(name, args)`. Tool names are the bare server names (`ask_human`, `set_away_mode`, `leave_conversation`, `notify_human`) — the `mcp__switchboard__` prefix is Claude-Code-side namespacing. Loopback peer → Bearer exempt (`http_auth.py` loopback exemption), so no token handling.
- **Firebase:** `firebase_admin` initialized from the same env resolution the server uses (`FIREBASE_SERVICE_ACCOUNT_JSON` + `FIREBASE_DATABASE_URL`, `.env` fallback — reuse `server.config.load_config` or a minimal dotenv read; plan decides). Reads for assertions; writes only to: `/answers/<conv>/<req>` (the phone's exact write shape), `meta/hidden`, `global_settings/away_mode` (restore fallback only).
- **Restart driver:** subprocess PowerShell invocation of the existing script with `-SkipTests` (mandatory — the default gate would run pytest against a stopped service); liveness determined solely by the harness's own `/healthz` polling.
- **Structure:** `scripts/smoke/smoke.py` (runner + flows) plus at most one helper module (`scripts/smoke/_smoke_lib.py`) if the file grows past comfortable review size. Flows register in an ordered list — chunk 2 appends a T-230 guard-trip flow; chunk 8 appends sweep assertions.
- **Report:** numbered flow lines (`FLOW 3 PASS — live ask/answer round-trip (2.1s)` / `FLOW 4 FAIL — expected parked==1, observed 0`), a summary block, exit 0 on all-pass else 1. Flags: `--skip-restart`, `--force`, `--keep`, `--base-url` (default `http://127.0.0.1:9876`).

## Non-goals (v1)

- **No FCM-delivery assertion.** Pushes fire; the phone ding is the accepted evidence. Asserting delivery would need a device-side witness — out of scope.
- **No Win32/Watchtower coverage** (chunk 6's manual smokes stand) and **no Operator DOM coverage** (node tests + reviewer tracing stand).
- **No multi-agent flows** (`message_and_await_agent` talking-stick, combine, convene, spawn) — v1 proves the human-loop contract; agent-to-agent flows are a later extension if a chunk needs them.
- **No mocked unit tests of the harness itself.** It IS the live tier; mocking it would test the mock. Pure helpers stay simple enough not to warrant units. (Deliberate exception to the repo's test-everything habit, decided here.)
- **No CI wiring.** The harness is run on demand by John or an agent post-deploy; there is no CI environment with the service + Firebase + phone.

## Acceptance

- A full default run against a healthy deployed service passes all flows in under ~3 minutes, prints the per-flow report, exits 0, and leaves: one hidden Ended conversation (collected by the sweep within 72h), one `ended` session record (swept per retention), away mode restored to its pre-run state, crash counts unchanged.
- `--skip-restart` passes flows 0-3 + cleanup without touching the service process.
- A deliberately broken deploy (service stopped mid-run, or answers listener dead) produces a FAIL line naming the first violated assertion and exit 1 — no hang past the bounded timeouts, cleanup still attempted.
- Zero new dependencies in `pyproject.toml`; nothing under `tests/` changes; the default pytest suite is byte-identical in collection.

## Open items for the plan pass

- Whether `server.config.load_config` is reusable as-is for env resolution (it validates more than the harness needs) or a 10-line dotenv read is cleaner.
- Exact bounded-timeout values per assertion (Firebase round-trips ~100-500ms; restart ~15-60s; marker sweep cadence determines the Flow-5 dormancy timeout).
- Whether Flow 2's discovery polls `conversations/` shallow-index or per-child reads (cost negligible at this scale; pick the simpler).
- The marker-file write must match the sweep's expected filename/shape — ground `dispatch_session_end_markers` + `cli-session-end-hook.py` at plan time.

## As-built (executed 2026-07-14; READY TO MERGE, uncommitted)

Executed the same day in an in-place Opus 4.8 SDD run (John chose run-here over the recommended fresh session; 4 tasks, sonnet implementers / opus reviews, zero escalations). Deliverables: `scripts/smoke/_smoke_lib.py` + `scripts/smoke/smoke.py` (LF/tabs/pure-ASCII) + a CLAUDE.md Testing subsection. Suite 839 unchanged; nothing under `server/`/`tests/`; no plugin bump. Acceptance met with both John-approved live runs: `--skip-restart` (flows 0-3 PASS, flow 4 SKIP, cleanup PASS, exit 0) and full (all flows PASS, exit 0, ~65s — under the 3-minute bar), leaving exactly the specified residue (one hidden Ended conversation + one ended session for the 72h sweep, away restored, crash counts unchanged).

Plan-vs-reality deviations (full detail in the plan's `## Deviations`): (1) the plan defined `load_env`/`init_firebase` but never called them — `ctx.fb_app` stayed `None` and every `rtdb()` in Flows 1-5 would have crashed on first live use; fixed by initializing in `main()` gated on `not --preflight-only`. The gap was invisible to per-task verification because only the HTTP-only `--preflight-only` mode was runnable pre-live. Process lesson: a plan that provides an init helper must also show its invocation at the call site. (2) Cleanup is not a sixth FLOWS entry — it is the fail-fast exception, run from `main()`'s `finally` under `rep.flow("cleanup")`; FLOWS = 5. (3) Harness report strings use ASCII hyphens (no-em-dash-in-code rule; also avoids cp1252 console mojibake). (4) The `mcp` SDK's `streamablehttp_client` 3-tuple matched the plan's assumption — no adaptation.

**Correction to §Flows, Flow 4:** the spec said the blocked `ask_human` "DIED with a transport error (the error is asserted, not tolerated)". Live reality: the severed call does NOT error promptly — it HANGS, and what fires is the harness's own bounded `wait_for` (15s, shortened from 30 post-review) raising TimeoutError. The assertion is correctly "the task must not return a clean answer"; the real proof of restart survival is `pending.parked == 1` plus the parked drain. Any future design reasoning about stateful-HTTP severance should assume hang, not error. (Benign noise: the SDK prints `Session termination failed: 404` on post-restart session close — SDK-emitted, not harness code.)

Accepted minor (reviewer-confirmed bounded): Flow 3 can orphan its blocking-ask task on its own assertion-failure path; cleanup's conversation end terminates the dangling ask server-side.
