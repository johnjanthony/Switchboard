# Parked asks: a restart or a killed session keeps the question (design)

**Date:** 2026-10-12
**Status:** design approved by John 2026-10-12 in chat; written spec awaiting his review.
**Backlog:** T-291 (a restart cancels blocking asks) and T-286 (session telemetry chunk 1 review minors), as one chunk ahead of T-255.

## Goal

A blocking `ask_human` whose waiter disappears without a client cancel keeps its question. Today a service restart cancels it (T-291, a regression since `32b6f39`), and a hard-killed session leaves it live but unreachable while the roster shows the session `awaiting_human` until the ask times out (T-286). After this chunk both cases park the question: it stays answerable on the phone and Operator, and John's answer reaches the session as a notice when it is resumed. An Esc still cancels, exactly as today. The four other T-286 minors ride along.

Done means: a full smoke run, restart included, passes flow 7 (restart survival) and the new killed-session flow; the Esc tests in `tests/test_progress_keepalive.py` pass unchanged; every suite is green.

## Decisions (John, 2026-10-12)

1. **One chunk:** T-291 with all of T-286, before T-255. T-286's killed-session item and T-291 ask the same question (park or cancel when the waiter vanishes) of the same code.
2. **A killed session's live ask is parked**, not cancelled and not left live.
3. **The cancel arm tells a stop from an Esc by a flag raised when the stop signal arrives**, not by detecting the client's `notifications/cancelled` (couples to MCP SDK internals, and changes a clean `/exit`) and not by deferring the cancel writes (racy).
4. **The killed-session behaviour gets a new smoke flow in the full run**, with a skip flag, rather than a one-off probe or an opt-in flow.

## Facts the design rests on (verified 2026-10-12 at 38c6c19)

- `ask_human`'s wait arm (`server/gateway/handlers.py`, the `except asyncio.CancelledError` after `asyncio.wait_for`) runs `terminate_pending`, which pops the record and calls `backend.mark_question_cancelled`; that sets the card's `cancelled` flag and removes the `pending_questions` record. Hydration (`server/hydration.py`, step 2b) parks only surviving, uncancelled `pending_questions` records.
- A service stop cancels in-flight tool calls through that arm. A throwaway probe (FastMCP stateful HTTP on uvicorn 0.44.0 and mcp 1.27.0, a blocking tool wrapped like `_await_with_progress_keepalive`, stopped by attaching to its console and sending `CTRL_C_EVENT` as NSSM's console stop does) logged `handle_exit` at t, uvicorn's `shutdown` at t+24 ms and the tool's cancel at t+137 ms. The cancel came from the lifespan teardown of the MCP session manager.
- **Unexplained:** the live 19:51 restart (`logs/nssm-stderr-20261012T195135.830.log`, the only restart today with an ask in flight) logged seven `ASGI callable returned without completing response` lines before uvicorn's `Shutting down`. The probe logged none. The design therefore records the flag's state at every cancel (Section 1) rather than assume the probe's order holds live.
- NSSM stops this service with the console Ctrl+C and the default 1500 ms wait (`AppStopMethodSkip` 0, `AppStopMethodConsole` 1500). uvicorn's `capture_signals` installs `handle_exit` for SIGINT, SIGTERM and SIGBREAK with `signal.signal`; `_run`'s `loop.add_signal_handler` raises `NotImplementedError` on Windows, which is swallowed.
- The mod's inbox poll is a `$.clock.every(POLL_MS)` timer (`startPoller`, `plugin/hooks/switchboard.ts`), independent of tool calls, so a session blocked in `ask_human` keeps polling.
- `SessionRegistry.sweep` skips an `awaiting_human` or `awaiting_agent` session backed by a live structure before it looks at the poll stamp. `_session_sweep_once` (`server/gateway/dispatch.py`) builds `live_ask_ids` from pendings that have a future.
- `handle_session_end` (`server/cli_session_end.py`) already keeps a parked question on a resumable end (logout, other) and terminates it only on a permanent one (`/clear`, compact). A live future there is cancelled, and the ask's own arm then cancels the card.
- Session notices persist through the `sessions/` mirror (`pending_notices`) and hydrate back.
- `Registry.add_parked` builds a future-less record but does not carry `notices`. `pop_record` is identity-checked.
- The Claude Code engine offers the mod no platform or home-directory signal.
- `turn.step` calls `telemetryFor`, which reads `$.session.id()`, then `noteModel` calls `telemetryFor` again: the doubled read.
- `apply_telemetry` bumps `last_event_at` on every report, and `_fire_mirror` writes whenever the canonical payload changes, `last_event_at` included.

## Section 1: a stop leaves blocking asks for hydration (T-291)

- **`ServiceStop`** (new, `server/service_stop.py`): a `requested` flag and the monotonic time it was first raised, with `raise_()` (idempotent, keeps the first time) and `age_ms()`. `_run` creates one and hands it to the handler builder.
- **`SwitchboardServer(uvicorn.Server)`** (new, `server/main.py`): `handle_exit` raises the flag, then delegates to uvicorn. `_request_stop` raises it too.
- **The wait arm.** In its shielded block, with the flag up, skip `terminate_pending` and `_safe_mark_cancelled`; with it down, run today's code. Either way `mark_wait_cancelled` still runs (the agent's call really is over) and the cancel re-raises. Every pass through the arm writes one audit line: `ask_human_cancelled: request_id=... conversation_id=... stopping=<bool> stop_age_ms=<n or none>`. With the flag up, the in-memory record dies with the process, and the card and `pending_questions` record survive for hydration to park.
- **Unchanged:** the setup-write arm (an ask still being written may have no `pending_questions` record to park, so it keeps cancelling the card; the window is milliseconds), the peer-wait arm of `message_and_await_agent` (wait queues never survive a restart), background asks (already future-less) and hydration.
- **If the order does not hold live:** a flow 7 failure whose audit line shows `stopping=False` means the cancel beat the signal on the live service. Stop and report; do not widen the rule.

## Section 2: a killed session's ask is parked (T-286)

- **Sweep rule.** In `SessionRegistry.sweep`, the awaiting exemption applies only to a session with no poll stamp since the server started. A session that has polled is judged by its poll alone: lost after `poll_lost_after_seconds` without one, live ask or not. The grace periods after a start or a sleep are unchanged. Never-polling sessions (a `-p` run, a desktop session on a mod copy that predates polling) keep today's exemption and silence rule.
- **`Registry.park_live(record)`** (new): identity-checked like `pop_record`. It replaces the blocking record in its slot with a future-less copy carrying `sender`, `request_id`, `msg_id`, `question`, `started_at` and `notices`, fires no pending-mirror delta, and returns the new record, or `None` when the slot no longer holds `record`.
- **`park_pending(registry, logger, record)`** (new, `server/gateway/pending_lifecycle.py`, the module that owns settlement): skips a record whose future is done, calls `park_live`, resolves the old future with `PARKED_SENTINEL` (new, `server/registry.py`, beside `SUPERSEDED_SENTINEL`) and logs `pending_parked_session_lost: conversation_id=... request_id=... cli_session_id=...`. It writes nothing to Firebase: the card and `pending_questions` record stay as they are, so a later restart parks the question again.
- **`_session_sweep_once`**, after `sweep`: every blocking pending with an unsettled future whose session is now `lost` goes through `park_pending`.
- **Concurrency.** The swap is synchronous, with no await between the identity check and the replacement, so an answer landing concurrently either resolves the live future first (and `park_pending` skips the done future) or meets the parked record.
- **`ask_human`** maps `PARKED_SENTINEL` to `{"status":"parked"}` through `_terminal_envelope` and returns before the answered-path side effects (no `remove_pending_question_record`, no resolved log), as it does for the other terminal sentinels.
- **The answer** takes the existing parked path: `dispatch_responses` resolves the parked record, and `finish_parked_resolve` removes the `pending_questions` record and queues the answer notice, delivered on the session's next inbox poll (a Resume keeps the session id). If the session record is pruned at retention first, the answer is still in the conversation history.
- **A falsely lost session** (a poll gap over 90 s while the process lives, such as a long network blip) is the only reader of the `parked` envelope. Its SKILL.md line: the question stays open on John's phone, his answer arrives as a session message, do not re-ask. A re-ask supersedes the parked record, as any new ask from the session does.
- **Unchanged:** a clean `/exit` mid-ask (`session.end` cancels it, as today) and background asks.

## Section 3: the other T-286 minors

- **`/rename` fallback** (mod). `refreshTitle` also uses the `/rename` args when no transcript is found, not only when the read throws.
- **Which home holds the transcript** (mod). `claudeConfigDir` becomes a list of candidate roots: `CLAUDE_CONFIG_DIR` alone when set, otherwise `HOME` and `USERPROFILE` (deduped), each with `/.claude`. `findTranscript` takes the first root whose `projects/` holds `<session-id>.jsonl`. Session ids are UUIDs, so no root can match the wrong session, and no platform guess is needed.
- **The doubled `$.session.id()` read** (mod). `noteModel` takes the `Telemetry` record `turn.step` already has, instead of calling `telemetryFor` again.
- **Two title scans at once** (mod). The `Telemetry` record gets an in-flight marker. A scan without `/rename` args returns at once while another scan runs; a `/rename` scan always runs, since it must read the row it just wrote.
- **A mirror write per telemetry post** (server). `apply_telemetry` still bumps `last_event_at` in memory, the proof of life for sessions that never poll, but fires the mirror only when the report changed a field or created the record. `test_an_identical_report_does_not_rewrite_the_mirror` then holds under a moving clock. The cost: after a restart, a never-polling session's hydrated `last_event_at` can be older than the truth; its status posts still mirror.

## Smoke flow: a killed session parks its ask

A new flow, "killed session parks its ask", runs before "restart survival" (whose restart clears every poll stamp), and `--skip-killed-session` skips it the way `--skip-restart` skips flow 7.

1. A second identity, with its own `cli_session_id` and sender, polls `GET /sessions/{sid}/inbox` a few times, so the session holds a poll stamp.
2. From its own MCP session it starts a blocking `ask_human` (away mode is already on for the run) and waits for the `pending_questions` record.
3. It stops polling but keeps the MCP call open, which is the falsely-lost path; the server side is the same as a kill. The flow then waits, bounded at 300 s (the 90 s threshold, a 60 s sweep interval and up to 90 s of start grace if the service started recently), for `sessions/<sid>` to read `lost` and the blocked call to return `{"status":"parked"}`.
4. It asserts the card is not cancelled, the `pending_questions` record is still present and `/healthz` `pending.parked` rose by at least one.
5. It writes an answer as flow 7 does, then polls that session's inbox until the answer notice arrives (the poll also revives the session).
6. Cleanup ends the identity's session (`POST /session_end`, reason `other`) and leaves its conversation.

The cost: a full run gets about 2.5 minutes longer (about 4 when the service started just before it), holds away mode on that much longer and pings the phone once more.

## Testing

Unit tests, written and run one at a time:

- **Server:**
  - `SwitchboardServer.handle_exit` and `_request_stop` raise the flag before delegating; `raise_()` keeps the first time.
  - The wait arm with the flag up, cancelled through an `anyio.CancelScope` whose cleanup awaits (the pattern that caught the `32b6f39` bug): no `mark_question_cancelled`, no record removal, `mark_wait_cancelled` still called, the cancel re-raised and the audit line written with `stopping=True`.
  - The wait arm with the flag down writes the audit line with `stopping=False`; the Esc tests in `tests/test_progress_keepalive.py` pass unchanged.
  - The setup-write arm with the flag up still cancels the card.
  - The sweep: a polled session holding a live ask goes `lost` after the poll threshold; a never-polled one holding a live ask stays exempt; nobody goes lost during grace.
  - `park_live`: the identity check, every field carried over (`notices` included), no pending-count change; `park_pending` skips a done future and leaves background asks alone.
  - `ask_human` maps `PARKED_SENTINEL` to the `parked` envelope without removing the `pending_questions` record.
  - One test from a killed session through the sweep's park to an answer and the queued notice.
  - `apply_telemetry`: an unchanged report under a moving clock bumps `last_event_at` but writes no mirror; a changed one writes.
- **Mod** (`claude plugin test plugin`): the `/rename` not-found fallback, the transcript found under the second home root, one `$.session.id()` read per main-thread `turn.step`, and a second title scan skipped while one runs while a `/rename` scan still runs.
- **Gates:** `pytest` with an explicit fresh `--basetemp`, `claude plugin test plugin`, `claude plugin validate plugin/.claude-plugin/plugin.json` and `bash scripts/mod-typecheck.sh`.

## Deploy and live checks

John-gated where the standing rules say so.

1. `restart-service.ps1 -SkipTests`, then `smoke.py --preflight-only`.
2. `scripts/deploy-mod.ps1` after the commit that touches `plugin/`.
3. A full smoke run with the restart, at John's go: it severs live MCP sessions, toggles away mode and pings the phone. Flow 7 and the killed-session flow pass, and `logs/switchboard.jsonl` shows `ask_human_cancelled ... stopping=True` for flow 7's ask, with its stop age.
4. If flow 7 fails with `stopping=False`, stop and report (Section 1).

## Docs and tracking

- `CLAUDE.md`: the Inbox paragraph's sweep sentence (a polling session goes lost without a poll even while it holds a live ask, whose question is then parked), and `parked` among the `ask_human` outcomes in the MCP tool surface section.
- `plugin/skills/switchboard/SKILL.md`: the `parked` envelope.
- `scripts/smoke` usage text and `CLAUDE.md`'s smoke harness paragraph: the new flow and its skip flag.
- At the end: T-291 and T-286 cut from the backlog, a PROJECT-JOURNAL entry, the as-built folded into `docs/switchboard-design-spec-comprehensive.md` and an As built section here.

## Out of scope

- A clean `/exit` mid-ask, which still cancels.
- The peer wait of `message_and_await_agent`.
- T-249 (dormancy for a lost session's member), T-032 and T-142.
- Explaining the seven early `ASGI callable` lines beyond the audit evidence Section 1 collects.
