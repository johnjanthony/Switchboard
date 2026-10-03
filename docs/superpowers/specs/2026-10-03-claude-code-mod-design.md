# Claude Code mod: phone wake and in-process hooks (design)

**Date:** 2026-10-03
**Status:** approved design (brainstorm with John, this session); spec awaiting John's review
**Backlog:** none yet; new work from the 2026-10-03 research into Claude Code mods. Follow-ups listed under Out of scope.
**Evidence:** vault note `Reference/Claude Code Mods` (C:\Work\ClaudeObsidian\Claude Vault), probe mod and logs at `C:\Work\Claude-Scratch\switchboard-mod-probe`.

## Goal

Two things, one mod:

1. **A phone or Operator message wakes an idle session.** Today a message to a session that is sitting at its prompt waits until someone types there. With this change it starts a turn within about 2 seconds, at the desk or away, which makes the phone a working remote control for sessions in place of Claude Code's Remote Control (not available to John). The same channel carries a stop that cancels a running turn; the phone and Operator buttons for it come in a follow-up.
2. **The Switchboard Claude Code hooks move in-process.** The seven Python command-hook entries become function hooks in a Claude Code mod shipped inside the Switchboard plugin, keeping their behaviour, with one change: in away mode, AskUserQuestion is answered from the phone instead of being denied.

## Facts the design rests on (verified 2026-10-03)

From the probe mod on Claude Code 2.1.288 (Windows terminal), its type declarations, and reading the Switchboard code:

- **Mods load here.** Mods are on by default from 2.1.287. The org guard admitted a user mod: `plugin.register: sb-probe (user, sb-probe@inline), judged by cc-plugin-sec-default: admitted`.
- **A timer-driven `$.prompt.submit` wakes an idle session.** A `$.clock.after(20_000)` callback calling `$.prompt.submit` fired at exactly 20 s; a turn started 342 ms later and the model answered. The call resolves as the turn starts.
- **A mod's own `prompt.submit` hook does not see its own `$.prompt.submit`** (contrary to the docs). It does see typed prompts.
- **`session.measure` can fire before `session.start`** (about 270 ms earlier, in both interactive sessions).
- **`session.end` gets a 1.5 s wall-clock bound** for its whole chain, running through `$` waits. A localhost `$.http.fetch` took 2 ms and the writes around it landed on `-p` exit, `/exit` and a closed terminal tab (one sample each).
- **Long `$` calls cost a hook nothing.** A hook's own budget is 10 s, but awaited `$` calls do not count against it (types). In the probe a mod-served tool awaiting a 150 s `$.process.run` returned inline, unaborted. A long-blocking `$.mcp.call` is not yet tested (see Testing).
- **`classic.<Event>` hooks keep the settings-hook contract.** A `classic.Stop` hook answers `{ block: reason }` (`ClassicResult` in the 2.1.288 types). `classic.SessionStart` carries `source`; `/clear` raises `session.end` with reason `clear` and no `session.start`.
- **A `tool.call` hook can attach model-only text:** `ToolCallResult` has `context`, "what the model reads after the tool's result".
- **Today's hooks cost a Python process each.** `hooks/hooks.json` starts three per tool call (status twice, injector once), two at turn end and one per prompt. The debug log timed the classic `UserPromptSubmit` chain at 413 ms with two Python hooks in it.
- **Notice delivery today.** `SessionRegistry.pending_notices` (persisted, hydrated on restart) is popped by `/agent_status` on UserPromptSubmit (always) and PostToolUse (except antigravity), and by `/away-mode?session_id=` for the Stop hook. Phone messages are queued as `John (from phone): <text>` (`server/inbound.py`) whether or not away mode is on. A session idle at its prompt fires no hook, so nothing delivers until the next typed prompt.
- **`ask_human` needs no setup from the caller.** For an unbound session it resolves or creates the conversation (`_resolve_conversation_and_member`) and attributes the question under the member's name (`_canonical_sender`).
- **The smoke harness uses the SessionEnd marker sweep** (`scripts/smoke/_smoke_lib.py:140`).
- **uvicorn logs every request** at `log_level="info"` into `logs/nssm-stdout.log`.
- **`handle_session_end` is fast:** registry work inline, Firebase writes via `_spawn_bg`.

## Decisions (settled with John)

1. **Claude Code only; agy is being abandoned.** No second implementation for agy. Removing agy is a separate job; until then the agy files this design does not touch keep working.
2. **Wake always, at the desk or away.** Sending from the phone is a deliberate act; away mode governs the agent-to-John direction. Rejected: wake only in away mode.
3. **Stop: plumbing now, UI later.** The server-to-mod channel carries stop from day one and the mod acts on it; the Android and Operator buttons come in a follow-up spec. Rejected: end to end now (touches all three codebases), drop it.
4. **AskUserQuestion in away mode is answered from the phone**, through `ask_human` called with `$.mcp.call`. Rejected: port today's deny. The deny is the fallback if the long-blocking `$.mcp.call` probe fails (see Testing).
5. **Short polling, every 2 s.** Rejected: long polling (`$.http.fetch` exposes no timeout and its default is unknown; needs a per-session waiter on the server), a streamed child process (`curl` dependency, an SSE route, a child per session). Long polling stays a cheap upgrade later: one URL and one server handler.
6. **Session end is a direct POST.** The marker sweep stays on the server until the agy cleanup, because the smoke harness uses it.
7. **The Python hooks are deleted, not kept as a fallback.** A Claude Code session runs the mod or, until relaunched after the plugin update, the old hooks.

## Design

### 1. Plugin layout

`hooks/hooks.json` changes from the classic `hooks` map to:

```json
{
	"modules": ["./switchboard.ts"]
}
```

Mod units, one job each, under `hooks/`:

| File | Job |
|---|---|
| `switchboard.ts` | `register`: wires events to the units; holds the injector. |
| `client.ts` | Every HTTP call to the server: base URL from `SWITCHBOARD_BASE_URL` (default `http://127.0.0.1:9876`), `Authorization: Bearer` from `SWITCHBOARD_TOKEN` when set. |
| `inbox.ts` | The 2 s poller and the delivery rules (section 3). |
| `status.ts` | The agent-status mapping, ported from `agent-status-hook.py`. |
| `turn-end.ts` | The `classic.Stop` away-mode check. |
| `ask.ts` | The AskUserQuestion bridge (section 4). |

The session state the mod keeps lives in `$.state` under the plugin name `switchboard`, declared in a contract at `types/index.d.ts` and named in `.claude-plugin/plugin.json` as `"types"`: the held items (`string[]`), the busy flag, the running turn's id, and whether the 401 warning has been shown. `$.state` survives a hot reload; module variables do not.

Deleted: `scripts/agent-status-hook.py`, `scripts/cli-session-injector-hook.py`, `scripts/away-mode-tool-guard-hook.py`, `scripts/cli-session-start-hook.py`, `scripts/cli-session-end-hook.py`. Kept until the agy removal, because agy's root `hooks.json` calls them: `scripts/turn-end-hook-away-mode.py`, `scripts/_hook_common.py`, `scripts/agy-identity-hook.py`.

### 2. Hook mapping

| Today | Mod event | Behaviour |
|---|---|---|
| SessionStart: `POST /session_start` | `classic.SessionStart` | Same body: `session_id`, `cwd`, `source`. `classic.SessionStart` rather than `session.start`, because only it carries `source` and `/clear` raises no `session.start`. |
| Injector (PreToolUse) | `tool.call`, filtered in the hook on `e.tool` starting `mcp__switchboard__` | `next({ ...e, cli_session_id, cwd })` from `$.session.id()` and `$.session.cwd()`. |
| Status on PreToolUse | `tool.call`, before `next` | `ask_human` posts `clear`, `message_and_await_agent` posts `waiting`, anything else `tool:<name>` with the same per-tool detail (Bash command, file name, URL host, Glob/Grep pattern; 200-char cap). Fire-and-forget. |
| Status on PostToolUse | `tool.call`, after `await next(e)` | Posts `thinking`. Fire-and-forget. |
| Status on UserPromptSubmit | `turn.start` on the main thread | Posts `thinking` with event `UserPromptSubmit`. `turn.start` catches typed and submitted turns alike, which the mod's own `prompt.submit` hook would not. |
| Notices on UserPromptSubmit / PostToolUse | `prompt.submit` adds held items as `context`; the main-thread `tool.call` returns `{ ...ran, context }` | Main thread only (`e.agentId` absent), so a subagent never receives John's message. |
| Stop hook | `classic.Stop` | Section 3. Posts status `clear`. |
| AskUserQuestion guard | `tool.call` on `AskUserQuestion` | At the desk `next(e)`. Away: section 4. |
| SessionEnd marker | `session.end` | Section 5. |

Status POSTs keep sending the event names the server already maps (`map_hook_event_to_state`), so `/agent_status` needs no change beyond its pop rule.

**Busy or idle:** the main thread's `turn.start` sets busy and records the turn id; its `turn.complete` clears both. Turns carrying an `agentId` are a subagent's and are ignored.

### 3. Inbox, wake and stop

**Server.**

- `GET /sessions/{sid}/inbox` returns `{ "notices": [...], "stop": bool, "away": bool, "pending_ask": bool }`. It pops the session's queued notices and its stop, and reads `away` (the global flag) and `pending_ask` (`registry.live_blocking_pending` for the session's bound conversation) fresh. It is the mod's only read route. An unknown session gets no notices and no stop, with `away` still read.
- `POST /sessions/{sid}/stop` records a stop with its time, in memory on `SessionRegistry` and never on the persisted record, so a restart drops it. The inbox reports a stop only while it is under 30 s old, so a stale stop cannot cancel a turn that started later. This is the route the later Android and Operator buttons call.
- `/agent_status` pops notices only for `cli == "antigravity"` on UserPromptSubmit. `/away-mode` is unchanged (agy's turn-end hook). Both pops go with the agy removal.
- A `logging.Filter` on `uvicorn.access` drops the inbox route's lines.
- Both new routes sit behind the existing `TokenAuthMiddleware`: loopback is exempt, WSL sends the bearer token.

**Mod (`inbox.ts`).**

- The poller starts on `session.start`, which also fires after a hot reload, as `$.clock.every(2000, ...)`. Each tick asks for the inbox of `$.session.id()`, so a `/clear` id change carries over. A tick is skipped while the previous one is in flight.
- On each answer:
  - **stop:** if busy, `$.turn.abort({ turnId })` and `$.ui.log("Stopped from phone")`; if idle, discard it. An abort the engine rejects (the turn already ended) goes to the debug log.
  - **notices:** append to the held list.
  - **idle with held items:** set busy, then `$.prompt.submit({ text: held.join("\n\n") })` and clear the list. Busy is set first so the next tick cannot submit twice. If the submit rejects, the items go back on the held list, busy is cleared, and the failure goes to the debug log, so the next tick retries. The text already reads `John (from phone): ...`, and the prompt is framed as sent by the switchboard plugin (no `asUser`).
- While busy, held items leave with whichever comes first: the next main-thread tool result (`context`), a prompt John types (`context`), or the turn-end block.

**Turn end (`turn-end.ts`, `classic.Stop`).** One inbox call (its notices join the held list), then:

- held items: `{ block: <held items joined> }`, and the list is cleared;
- otherwise away with no live ask: `{ block: REDIRECT_REASON_AWAY_MODE }`, today's text verbatim;
- otherwise (at the desk, or away with a live ask): pass, as today.

When there are held items and away mode is on with no live ask, the block carries both, as today: the notices first, then the redirect text.

**Aborting a blocking `ask_human`:** the turn is cancelled and Claude Code's existing MCP cancellation (stateful HTTP, `notifications/cancelled`) marks the question cancelled on the phone.

**Delivery is at most once**, as today. A notice popped in the instant before a crash or `/exit` is lost; keeping held items in `$.state` covers reloads.

### 4. AskUserQuestion bridge (away mode)

On a `tool.call` of `AskUserQuestion`, `ask.ts` reads `away` from the inbox (whose notices join the held list, as on any poll). At the desk it passes `next(e)`. Away, it answers the call itself:

- Each entry of `e.questions` (1 to 4) becomes one blocking `$.mcp.call("switchboard", "ask_human", { question, suggestions, sender: "Claude", cli_session_id, cwd })`, one after another. `suggestions` are the option labels. The header and any option descriptions are folded into the question text, which is numbered `k of n` when there are several. A multi-select question adds "pick one or more, comma-separated".
- The replies return as `{ result: { questions: e.questions, answers: { [question]: reply } } }`. Free text goes through as typed.
- `$.mcp.call` is the plugin's call, so the mod's own injector does not see it: the mod passes `cli_session_id` and `cwd` itself.
- A terminal sentinel from `ask_human` (timeout, `conversation_ended`, superseded) or a failed `$.mcp.call` ends the call with `{ deny: <the reason> }`, so the model sees what happened.

### 5. Session end

`session.end` POSTs `{ session_id, reason }` to a new `POST /session_end`, which calls `handle_session_end` with the same arguments the marker sweep passes and answers 200. One attempt, inside the 1.5 s bound. A missed POST degrades to the silence sweep marking the session lost after about 15 minutes. `/clear` ends the old id the same way, as today's SessionEnd marker does.

The marker sweep (`dispatch_session_end_markers`) and `SWITCHBOARD_MARKER_DIR` stay until the agy cleanup.

## Error handling

The server being down never breaks a Claude Code session.

| Unit | Server unreachable or erroring |
|---|---|
| Injector | Never calls the server. |
| Status POSTs | Fire-and-forget; a debug-log line (`$.ui.log(..., { to: "debug" })`) per failure. |
| Inbox poller | Backs off from 2 s, doubling to 30 s, and returns to 2 s on the first success. Held items are kept. |
| Turn-end check | Still blocks with items already held (they are local); no away-mode block, failing open as today. |
| AskUserQuestion bridge | `{ deny }` with the error text, so the model can ask in the terminal instead. |
| Session end | One attempt; the silence sweep is the backstop. |

- **A 401** (WSL with a missing or wrong `SWITCHBOARD_TOKEN`) is written to the transcript once per session with `$.ui.log`, because otherwise a WSL session would silently never wake.
- **The mod failing to load** (a policy change, or a Claude Code update breaking the early-access API) is loud already: every switchboard tool call then fails with `cli_session_id required`. The pytest check under Testing catches API breakage before a session does.
- **Auto mode:** Claude Code denies a tool call whose input a hook changed after the classifier reviewed it. That applies to the injector exactly as it applies to today's PreToolUse injector; this design neither causes nor fixes it.

## Testing

**Probe first** (the plan's first task), in an interactive session with the mod loaded by `--plugin-dir`:

1. A `$.mcp.call("switchboard", "ask_human", ...)` that stays blocked past 2 minutes and then returns the reply. If it cannot, section 4 is replaced by porting today's deny (`away-mode-tool-guard-hook.py`'s reason text) and the rest of the design stands.
2. A main-thread `tool.call` hook returning `{ ...ran, context }` is accepted and the model reads the context.

**Mod tests** (`claude plugin test`, `*.test.ts`), with `mock.clock` and the test's own hooks answering `http.fetch`, `prompt.submit`, `turn.abort` and `mcp.call` beneath the mod:

- status mapping and detail extraction;
- the injector rewrite, and that it leaves other tools alone;
- the inbox rules: idle submits once, busy holds, held items attach to the next main-thread tool result and not a subagent's, stop aborts only when busy, backoff and recovery;
- turn-end decisions: held items, away with and without a live ask, at the desk, server down;
- the AskUserQuestion bridge: one question, several, multi-select, a sentinel turning into a deny, at the desk passing through;
- the session-end POST.

**Catching API breakage.** A pytest test runs `claude plugin validate` and `claude plugin test` on the plugin, skipped when `claude` is not on the PATH. The mod tests run against the installed Claude Code engine, so the normal `pytest` run after a Claude Code update catches a broken mod. The plan checks that `claude plugin test` from the repo root finds only the mod's tests.

**Server tests** (pytest): the inbox route (pops once, the 30 s stop window, `away` and `pending_ask`, an unknown session, bearer auth for a non-loopback peer), the stop route, the session-end route, `/agent_status` popping only for antigravity, and the access-log filter.

## Rollout and live verification

1. **Server changes, then a restart.** Backward compatible: Claude Code sessions still on the Python hooks lose mid-turn notice delivery but keep delivery at turn end through `/away-mode`.
2. **Plugin:** the mod units and contract, `hooks/hooks.json` switched to `modules`, the five Claude-Code-only scripts deleted. The `plugin.json` version needs a bump or the version-gated cache keeps serving the old hooks; that bump waits for John's direction.
3. **`/plugin` update.** New sessions load the mod; running sessions keep the Python hooks until relaunched, and the server serves both.

Live checks after deploy:

1. A phone message to an idle Windows session starts a turn within about 2 s, at the desk and away.
2. The same from a WSL session, with the token.
3. `POST /sessions/{sid}/stop` cancels a running turn.
4. In away mode, an AskUserQuestion with two questions is answered from the phone.
5. `/exit` marks the member dormant through the POST.
6. The sessions board's status rows update as tools run.
7. `smoke.py --skip-restart` passes.

**Docs:** CLAUDE.md (Layout, Hooks, Setup: Python and `SWITCHBOARD_MARKER_DIR` are no longer needed for Claude Code hooks), `skills/switchboard/SKILL.md` (AskUserQuestion works in away mode; phone messages arrive as prompts from the switchboard plugin), README.

## Out of scope

- The Android and Operator Stop buttons and their Firebase command path.
- An away-mode band and status line in the terminal, and forwarding terminal text to the phone.
- Telemetry from `session.measure` (context window, rate limits, cost) for the sessions records and Watchtower.
- Moving the switchboard tools from the MCP server into the mod.
- Slash commands that run without a turn (`/away`, `/sb status`).
- Using the inbox poll as a liveness heartbeat for the silence sweep.
- The agy removal, which also takes the marker sweep, the `/agent_status` and `/away-mode` pops, and `SWITCHBOARD_MARKER_DIR`.
