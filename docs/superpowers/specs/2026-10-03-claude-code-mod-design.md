# Claude Code mod: phone wake and in-process hooks (design)

**Date:** 2026-10-03
**Status:** approved by John 2026-10-03 (design and written spec); implementation plan `docs/superpowers/plans/2026-10-03-claude-code-mod.md` (disk-only, gitignored), approved the same day
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

Mod files under `hooks/` (as built 2026-10-03). The engine's load-time scan follows `$` and `$.state` references only within the module `hooks.json` names: a module that passes `$` into a function imported from another plugin file, or reads an atom declared in another file, does not load. So every function that takes `$`, and every atom, lives in `switchboard.ts`, and the other files hold the pure logic:

| File | Job |
|---|---|
| `switchboard.ts` | `register` and every hook; the server calls (base URL from `SWITCHBOARD_BASE_URL`, default `http://127.0.0.1:9876`, `Authorization: Bearer` from `SWITCHBOARD_TOKEN` when set); the 2 s poller and delivery rules (section 3); the turn-end check; the AskUserQuestion bridge (section 4); the `$.state` atoms. |
| `client.ts` | Pure: default base URL, request headers, inbox-body parsing. |
| `inbox.ts` | Pure: poll interval and backoff. |
| `status.ts` | Pure: the agent-status mapping, ported from `agent-status-hook.py`. |
| `turn-end.ts` | Pure: the turn-end block decision and the redirect text. |
| `ask.ts` | Pure: the phone wording, reply unwrapping (`$.mcp.call` returns switchboard replies as `{"result": "<reply>"}`), and the terminal-sentinel check. |

The session state the mod keeps lives in `$.state` under the plugin name `switchboard`, declared in a contract at `types/index.d.ts` and named in `.claude-plugin/plugin.json` as `"types"`: the held items (`string[]`), the busy flag, the running turn's id, and whether the 401 warning has been shown. The contract names each member inline in `PluginState` (the validator does not follow a type alias) and exports nothing else. `$.state` survives a hot reload; module variables do not.

Deleted, but only once every Claude Code session has relaunched onto the mod: running sessions call the plugin's scripts from the live repo (the marketplace is a `directory` source), so deleting them earlier breaks those sessions' hooks. `scripts/agent-status-hook.py`, `scripts/cli-session-injector-hook.py`, `scripts/away-mode-tool-guard-hook.py`, `scripts/cli-session-start-hook.py`, `scripts/cli-session-end-hook.py`. Kept until the agy removal, because agy's root `hooks.json` calls them: `scripts/turn-end-hook-away-mode.py`, `scripts/_hook_common.py`, `scripts/agy-identity-hook.py`.

### 2. Hook mapping

| Today | Mod event | Behaviour |
|---|---|---|
| SessionStart: `POST /session_start` | `classic.SessionStart` | Same body: `session_id`, `cwd`, `source`. `classic.SessionStart` rather than `session.start`, because only it carries `source` and `/clear` raises no `session.start`. |
| Injector (PreToolUse) | `tool.call`, filtered in the hook on `e.tool` starting `mcp__switchboard__` | `next({ ...e, cli_session_id, cwd })` from `$.session.id()` and `$.session.cwd()`. |
| Injector's `permissionDecision: "allow"` | `tool.check` on the same filter | Answers `{ decision: "allow" }`, so the switchboard tools stay pre-approved in sessions that are not in bypass mode, as the Python injector made them. |
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

- The poller starts on `session.start`, which also fires after a hot reload, as `$.clock.every(2000, ...)`, and only when `e.isInteractive`: a `claude -p` run is about to exit, so a notice popped into it would be lost. Each tick asks for the inbox of `$.session.id()`, so a `/clear` id change carries over. A tick is skipped while the previous one is in flight.
- **At the desk, a draft in the prompt box defers delivery.** When away mode is off and `$.prompt.read()` returns non-empty text, the items stay held and ride as `context` on the prompt John is typing. In away mode a draft never holds delivery back, since a draft left behind would otherwise block every phone message.
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
| AskUserQuestion bridge | Away mode unreadable: the dialog runs in the terminal, as at the desk. A failed `ask_human` call: `{ deny }` with the error text, so the model can ask in the terminal instead. |
| Session end | One attempt; the silence sweep is the backstop. |

- **A 401** (WSL with a missing or wrong `SWITCHBOARD_TOKEN`) is written to the transcript once per session with `$.ui.log`, because otherwise a WSL session would silently never wake.
- **The mod failing to load** (a policy change, or a Claude Code update breaking the early-access API) is loud already: every switchboard tool call then fails with `cli_session_id required`. The pytest check under Testing catches API breakage before a session does.
- **Auto mode:** Claude Code denies a tool call whose input a hook changed after the classifier reviewed it. That applies to the injector exactly as it applies to today's PreToolUse injector; this design neither causes nor fixes it.

## Testing

**Probe first** (the plan's first task), in an interactive session with the mod loaded by `--plugin-dir`:

1. A `$.mcp.call("switchboard", "ask_human", ...)` that stays blocked past 2 minutes and then returns the reply. If it cannot, section 4 is replaced by porting today's deny (`away-mode-tool-guard-hook.py`'s reason text) and the rest of the design stands.
2. A main-thread `tool.call` hook returning `{ ...ran, context }` is accepted and the model reads the context. If not, held items leave only with a typed prompt or the turn-end block.
3. A `$.http.fetch` started without `await` in a hook completes after the hook returns. If not, the status POSTs are awaited instead (a localhost round trip is about 2 ms).

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

## As built (2026-10-03)

Commits `645a85d..32b6f39` on `develop`; plugin `2.0.0`. Where this section and the design above differ, this section is what shipped.

**Task 1 probe.** All three answers were yes in a headless run: a `$.mcp.call` held 150 s returned, a `tool.call` hook's `context` reached the model, and an unawaited `$.http.fetch` finished after its hook returned. The first answer did not hold interactively (see the AskUserQuestion bridge below).

**What the engine forced.**

- **One module.** The engine's load-time scan follows `$`, and `$.state` atoms, only within the module `hooks.json` names; a module passing `$` into a function imported from another file does not load. Every `$`-taking function and every atom lives in `hooks/switchboard.ts`; `client.ts`, `status.ts`, `inbox.ts`, `turn-end.ts` and `ask.ts` hold only pure logic. The `$.state` contract names its members inline in `PluginState` and exports nothing (the validator rejects `export {}` and does not follow a type alias).
- **No classic hooks, no `tool.check`.** The work org's security plugin `cc-plugin-sec-default` bypasses a user mod's `classic.*` and `tool.check` hooks, which `claude plugin test` cannot show. The turn-end check runs on a main-thread `turn.complete` that ended with an answer, and hands the agent its next turn with an unawaited `$.prompt.submit` (verified: the new turn starts about 200 ms after the hook returns) instead of a Stop block. Session start posts from `session.start`, without `source` (`/clear` raises none, so a cleared id is registered by its first status post). The switchboard tools are not pre-approved; John's sessions run in `bypassPermissions`.
- **Sessions load the plugin from the live repo.** The marketplace is a `directory` source, so a new session reads `hooks/hooks.json` and the mod from the working tree and a version bump gates nothing, while `${CLAUDE_PLUGIN_ROOT}` in a running session's command hooks also points at the working tree. Deleting the Python scripts broke this session's running hooks once; the deletion waited until every session had relaunched onto the mod. WSL runs its own clone as its own directory marketplace.
- **Server calls are bounded.** `$.http.fetch` has no timeout; every call races a 1.5 s timer (`FETCH_TIMEOUT_MS`).
- **AskUserQuestion bridge and the 120 s rule.** Claude Code moves the mod's own `$.mcp.call` to a background task at 120 s, as it does the model's MCP calls, and hands back its own text in place of the reply. The bridge recognises that text (`backgroundedTaskId`, wording pinned by a test), asks nothing further, and denies with the task id, the answers so far and the questions not yet asked; John's answer then reaches the model as that task's result. Each question also races the turn's `next.signal`, so an interrupted turn stops waiting and asks nothing more. Moving the bridge off MCP is backlog T-276.
- **`$.mcp.call` replies come wrapped** as `{"result": "<reply>"}`; `ask.ts` unwraps them.

**Found and fixed on the way.** Esc on a pending `ask_human` never marked the phone card cancelled. `_await_with_progress_keepalive` awaited the handler task unshielded inside the MCP responder's still-cancelled anyio scope; that await was cancelled at once and asyncio forwarded the cancel into the handler, cutting its shielded cleanup short after the in-memory record was popped (no Firebase flag, the `pending_questions` record left behind, nothing logged). The await now runs under a 10 s shield (`32b6f39`). It predates the mod and hit every blocking tool. Operator's apparent failure to show a cancelled card was the left-over `pending_questions` record, which its answer panel reads.

**Observed behaviour worth knowing.** `$.turn.abort` moves an in-flight Bash command to a background task rather than killing it, so a stop ends the turn but a running command finishes. A turn ending with no text is continued by Claude Code's own "no visible output" nudge before `turn.complete` fires.

**Live checks (2026-10-03).** All passed: wake at the desk and in away mode (Windows), wake from WSL with the token, stop (377 ms from POST to `Stopped from phone`), the away-mode redirect and the live-ask exception, AskUserQuestion away (answered within 120 s, and held past 120 s with the denial and the answer arriving as the task result), Esc during a bridged question (the server cancels it; after `32b6f39` the card shows cancelled on the phone and Operator), `/exit` on Windows and WSL (member dormant through the POST), the sessions board, and `smoke.py --skip-restart`. Also checked: delivery while busy arrives as tool-result context, a draft at the desk holds delivery and the message rides the typed prompt, AskUserQuestion at the desk stays in the terminal.

**Follow-ups.** Backlog T-275 to T-283: the Stop buttons, the bridge off MCP, the review's deferred minors, and the Out of scope items above.
