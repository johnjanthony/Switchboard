# The away-mode redirect: cap the loop, pre-approve the switchboard tools (design)

**Date:** 2026-10-13
**Status:** implemented 2026-10-13 (`380cdba..c1d6ac7`); the As built section at the end records where the build differs from this design. Written 2026-10-13 from the decisions John took after the probe report; implementation plan `docs/superpowers/plans/2026-10-13-away-mode-redirect-cap.md` (disk-only, gitignored).
**Backlog:** T-296 (the away-mode redirect in a session without a switchboard allow rule).
**Evidence:** T-284's live run r5 (`C:\Work\Claude-Scratch\T-284-live\r5-debug.log`, screens in `r5\history.txt`); the T-296 probe of 2026-10-13 on Claude Code 2.1.296 (Windows terminal; three interactive `default`-mode haiku sessions driven through a ConPTY, each loading a throwaway copy of `plugin/` with `--plugin-dir`), with its plugin copy, driver and logs in `C:\Work\Claude-Scratch\T-296-probe\` (`a-debug.log`, `b-debug.log`, `c-debug.log`, `hook.log`); the 2.1.296 mod declarations; and the code paths cited below.

## Goal

In away mode, the mod's turn-end check (`checkTurnEnd`, `plugin/hooks/switchboard.ts:410`) submits the redirect (`REDIRECT_REASON_AWAY_MODE`, `plugin/hooks/turn-end.ts`) as the next prompt after every main-loop turn that ends with an answer while the session holds no live blocking ask. An agent that answers the redirect in text ends that turn the same way, so the redirect fires again: in r5, about one model turn per second (48 turn ends in one minute, 53 in another) until someone pressed Esc.

This work:

1. **Caps the loop.** After two redirects in a row that the agent did not answer by asking, the mod stops redirecting, leaves the session idle, and tells John once on his phone.
2. **Pre-approves every `mcp__switchboard__*` tool**, so a `default`- or `auto`-mode session never prompts for its own `ask_human` (the prompt that started r5's loop) and the mod's own notice never prompts.
3. **Rewords the redirect** so a denied or failed `ask_human` no longer reads as a live question.

## What r5 shows

The loop did not begin with an agent ignoring the redirect at random. At 17:58:48 the session's own `ask_human` permission dialog was cancelled (`Aborting: tool=mcp__switchboard__ask_human isAbort=true`, `mcp_tool tool permission denied`). From then on every turn was one model request with no tool call (`turns=1 ... stop=end_turn`), answering "I'm waiting on John and not sending another ask_human." The agent believed a question was live, which the redirect's own paragraph about backgrounded asks ("do NOT call ask_human again") invites, while the server reported `pending_ask: false`.

## Facts the design rests on (probed 2026-10-13, Claude Code 2.1.296)

- **A rejected permission dialog ends the turn as an answer, not an interrupt.** Answering No, or pressing Esc, at the `ask_human` dialog gave `turn.complete` `reason: "answer"` with an empty `answer` (`a-debug.log`, both cases). So the turn-end check runs, and in away mode the redirect fires. CLAUDE.md's "an interrupted turn gets no check" holds for Esc during generation, not for Esc at a dialog.
- **A rejected call is not a `deny` in `tool.call`.** For No and for Esc, the mod's `next(call)` returned `{ ref, result, text, isError: true }` with "The user doesn't want to proceed with this tool use..."; `deny` was absent.
- **A call that ran has no `isError`.** An allowed `ask_human` at the desk returned `{ ref, result, text }` carrying the at-desk text (`{"result":"ERROR: John is at his desk. Ask this question via the terminal."}`), with `isError` absent.
- **`$.mcp.call` works from inside `turn.complete`.** A `notify_human` called there returned `{"content":[...],"isError":false}`. In a `default`-mode session it opened its own permission dialog ("Tool use · from the switchboard plugin") and the call waited on it (19 s, until answered).
- **The mod's own `$.mcp.call` passes through its own `tool.call` hook.** The probe's `tool.call` logging recorded the mod's `notify_human`. The comment at `plugin/hooks/switchboard.ts:365` ("$.mcp.call is the plugin's own call, so the injector never sees it") is stale.
- **A plugin's own `$.prompt.submit` runs through the mod's `prompt.submit` hook** (r5: `switchboard@switchboard ... prompt.submit settled` before each redirect turn), and the declarations give it `origin: { kind: 'plugin', name }`; a typed prompt arrives as `{ kind: 'composer' }` (probed).
- **`hooks.json` matchers split the `PermissionRequest` hooks cleanly.** With an entry matching `^mcp__switchboard__` whose command prints an allow, and the existing script's entry matching `^(?!mcp__switchboard__)`:
  - `ask_human` ran only the first; its allow landed 228 ms after `executePermissionRequestHooks` (`c-debug.log`), and the transcript row read "Allowed by PermissionRequest hook";
  - Bash ran only the second, and the server held and settled it as before (`approval_opened`, `approval_settled_at_terminal ... exact=True`, `approval_closed`);
  - the dialog drew for at most one 0.5 s screen frame (`b\history.txt`); the mod's own `notify_human` dialog never visibly drew;
  - `claude plugin validate` passed with the same warnings as the shipped plugin.
- **The hook input is compact JSON** (`"tool_name":"Bash"`), and a tool's arguments sit unescaped inside `tool_input`. So matching `tool_name` by grep in the script could match another MCP tool whose arguments include a `tool_name` key; the engine's matcher on its own tool name cannot be fooled that way.

## Decisions (settled with John, 2026-10-13)

1. **Cap the redirects** (option A), rather than detecting text-only turns (B) or both (C): one counter covers every non-compliant shape (text only, ending on `notify_human`, running tools without asking).
2. **The cap is 2:** one redirect, one retry, then stop.
3. **Pre-approve every `mcp__switchboard__*` tool,** not only `ask_human` and `notify_human`. This reverses the earlier choice that the mod not pre-approve them; it is done through the sanctioned `PermissionRequest` command hook, not the mod's `tool.check`.
4. **Reword the redirect's backgrounded-ask paragraph** (ride-along).
5. **Ride-alongs:** fix CLAUDE.md's "interrupted turn" claim and the stale `$.mcp.call` comment, and correct the memory note `project_live_dialog_checks` (Esc at a dialog does not stop the redirect).

## Design

### 1. The cap (`plugin/hooks/switchboard.ts`, `plugin/hooks/turn-end.ts`)

**The count.** A new `$.state` atom, `redirects` (a number, default 0), counts the bare redirects submitted since the session's last real input. A bare redirect is a turn-end submit carrying the redirect text and no held items. The atom joins `PluginState` in `plugin/types/index.d.ts`. `$.state` survives a hot reload and is reset by `/clear`, which starts a new chain, as it should.

**The turn-end decision.** In `checkTurnEnd`, when the inbox answer says away mode with no live ask:

- **Held items present:** submit them with the redirect, as today. The count is not touched here; the submit's own `prompt.submit` resets it (below), since phone messages are new input.
- **No held items, count below 2:** add one to the count and submit the bare redirect.
- **No held items, count at 2:** submit nothing. Clear `busy`, run `deliverIfIdle` as on any turn end, and send the cap notice (section 2). The session sits idle; its inbox poll keeps running, so John's reply from the phone reaches it.

When the answer says away mode is off, or a live ask is pending, the count resets to 0. When the inbox read fails, the count is left alone (nothing is submitted, as today).

The decision stays pure: `turn-end.ts` gains the cap constant (`REDIRECT_CAP = 2`) and a function that takes the held items, the inbox answer and the count and returns what to do (submit this text, stop at the cap, or nothing) and the new count. `checkTurnEnd` does the `$` work.

**The resets.** The count returns to 0 on:

- **any `prompt.submit` other than the mod's own bare redirect.** The mod's redirect is the one whose `origin` is `{ kind: 'plugin', name: 'switchboard' }` and whose `text` is exactly the redirect text. A typed prompt, a phone message the mod submits, a held-items-plus-redirect submit, a task notification (a backgrounded ask's result) and anything else reset it;
- **a blocking `ask_human` that ran:** a `tool.call` of `mcp__switchboard__ask_human`, without `background: true` in its input, whose result has no `deny` and no `isError`. This covers the model's calls and the AskUserQuestion bridge's own `$.mcp.call` (which passes through `tool.call`). A background ask does not reset it, because it does not satisfy the turn-end check either: an agent ending on background asks would otherwise loop forever;
- **a turn end with away mode off or a live ask pending** (above).

So the longest chain is: a turn ends, redirect 1, the agent ends without asking, redirect 2, the agent ends without asking, the cap notice. Three turn ends where r5 had one per second.

### 2. The cap notice

At the cap, the mod calls `notify_human` with `$.mcp.call('switchboard', 'notify_human', ...)`, passing `cli_session_id` and `cwd` itself as the AskUserQuestion bridge does, and `sender: 'Claude'`. It is not awaited inside `checkTurnEnd` (the session must go idle at once); a failure is logged to the debug log only. The message, in the session's own conversation:

> This session ended three turns in a row without asking you anything, so the away-mode reminder has stopped. It is idle now; reply here to give it its next instruction.

It is sent once per chain: the count stays at 2 until a reset, and an idle session raises no further turn ends without new input, which resets it. Section 3 means the call never prompts.

### 3. Pre-approving the switchboard tools (`plugin/hooks/hooks.json`)

`hooks.json` gets two `PermissionRequest` entries in place of one:

```json
{
	"matcher": "^mcp__switchboard__",
	"hooks": [
		{
			"type": "command",
			"command": "printf '%s' '{\"hookSpecificOutput\":{\"hookEventName\":\"PermissionRequest\",\"decision\":{\"behavior\":\"allow\"}}}'",
			"timeout": 10
		}
	]
},
{
	"matcher": "^(?!mcp__switchboard__)",
	"hooks": [ the existing permission-request.sh entry, unchanged ]
}
```

- A switchboard tool's dialog is allowed at once and never reaches the server, so it raises no approval, no card and no "needs approval" mark. That holds at the desk and away, in the main loop and in subagents, for the model's calls and the mod's own.
- Every other tool, `AskUserQuestion` and `ExitPlanMode` included, goes to `permission-request.sh` exactly as today.
- `permission-request.sh` is unchanged apart from its header comment, which says the switchboard tools never reach it.
- `bypassPermissions` sessions (John's default and both spawn paths) are unaffected: they open no dialog.
- **Security posture.** Any session with the plugin installed can now call every switchboard tool without a prompt, including `send_document_human` (a file upload to Firebase Storage, gated by the server's path validation, extension allowlist and secret-name denylist), `set_away_mode`, `combine_conversations` and `leave_conversation`. John chose this knowingly; the server's own validation remains the control.

### 4. The reworded redirect (`plugin/hooks/turn-end.ts`)

The backgrounded-ask paragraph becomes conditional on the harness's message, and says what a failed ask means:

> Only if your ask_human call came back saying it was moved to a background task (~120s) is a question still live: then do NOT call ask_human again (that would supersede it). Just end your turn: the pending question is your handback, this check will allow it, and John's reply arrives as the background task's result. An ask_human that was denied, rejected or returned an error is NOT a live question: call ask_human again.

The other lines are unchanged. The test pinning the first and last lines (`plugin/hooks/tests/turn-end.test.ts:32-33`) still holds. The redirect does not mention the cap, so an agent cannot plan around it.

`plugin/skills/switchboard/SKILL.md`'s "Backgrounded asks" item (line 35) gets the same condition and the same sentence about a denied or failed ask.

### 5. Error handling

- **Inbox read fails at turn end:** as today, nothing is submitted; the count is unchanged.
- **The cap notice fails** (MCP connection down, rate limit): debug log only. The session is idle either way; the roster still shows it.
- **The mod's notice while away mode turns off in between:** `notify_human` takes its at-desk path and still pushes, which is harmless.
- **The inline allow fails to run** (no bash): the same exposure `permission-request.sh` has today, which also needs bash. Not probed; the expectation is that a hook which prints nothing leaves the dialog to the terminal (the existing script relies on that), and `printf` cannot exit 2, the one exit code that blocks the call.

## Rejected alternatives

- **Detect text-only turns (B), or both (C).** B misses the `notify_human`-only loop the redirect text itself warns about, and any loop that uses tools without asking; C adds a second rule to save one cheap turn.
- **Pre-approve through the mod's `tool.check` hook.** It lands on 2.1.296, but the security plugin logs it as bypassed, and the vault note `Reference/Claude Code Permission Prompts from a Plugin` says not to build on it. The command hook is the sanctioned path.
- **Short-circuit inside `permission-request.sh` by grepping `tool_name`.** Probed and working, but the grep can match a `tool_name` key inside another tool's arguments, which would auto-allow a foreign tool. The engine's matcher reads the real tool name.
- **Send the cap notice through a new server route.** It would avoid the MCP call, but section 3 removes the only problem (the prompt) and the existing `notify_human` path needs no server change.
- **Reset the count with a flag set before the mod's own submit.** Origin plus text identifies the mod's redirect without state that a lost prompt could leave set.

## Out of scope

- The parked-asks races and other open mod items (T-292).
- Desktop sessions held at the desk (T-294) and ExitPlanMode from the phone (T-295).

## Testing

- **`claude plugin test plugin`** (`plugin/hooks/tests/`):
  - the pure decision in `turn-end.ts`: below the cap it submits and counts; at the cap it stops; held items submit whatever the count; away off or a live ask resets;
  - the session flow: two text-only answers after a turn end give two redirects, then no third submit and one `notify_human` call through `$.mcp.call` with the session's id and cwd;
  - each reset: a typed prompt, a phone message the mod submits, a blocking `ask_human` result without `isError`; and the non-resets: an `ask_human` result with `isError`, a background `ask_human`, the mod's own bare redirect;
  - the reworded redirect text, pinned.
- **pytest:**
  - `tests/test_mod_plugin.py` (or a new test beside it) reads `hooks.json` and checks the two matchers against tool names with the engine's regex semantics: `mcp__switchboard__ask_human` and `mcp__switchboard__send_document_human` match only the first entry; `Bash`, `AskUserQuestion`, `ExitPlanMode` and `mcp__other__tool` match only the second;
  - the first entry's command, run under bash, prints exactly the allow decision as valid JSON;
  - `tests/test_permission_request_script.py` stays green unchanged.
- **`bash scripts/mod-typecheck.sh`** and `claude plugin validate plugin/.claude-plugin/plugin.json`.
- **Live** (after the server is current and `scripts/deploy-mod.ps1` has run; away mode is John's to turn on):
  - a `default`-mode haiku session in the ConPTY driver, told to answer the away-mode reminder in text only: two redirects, then idle, and one notice on the phone; a phone reply then starts a turn;
  - a `default`-mode session calling `ask_human` in away mode: no approval card, the question card posts, and John's answer returns;
  - a Bash dialog in the same session still raises its approval card.

## Docs

- **CLAUDE.md:**
  - the "Turn end" bullet: the cap and its notice, the resets, and that Esc or No at a permission dialog ends the turn as an answer (so it gets the check);
  - the "Injector" bullet: the switchboard tools are pre-approved by the plugin's `PermissionRequest` hook;
  - the "Approvals" bullet: switchboard tools never reach the server.
- **`docs/switchboard-design-spec-comprehensive.md`:** §6 ("The switchboard tools are not pre-approved"), §6.1 and §8.2, folded in with the As built.
- **The stale comment** at `plugin/hooks/switchboard.ts:365`.
- **Memory** `project_live_dialog_checks`: Esc at a dialog does not stop the redirect; with this change a `default`-mode test session no longer prompts for `ask_human`. The vault note `Reference/Claude Code Permission Prompts from a Plugin` gains the matcher split and the rejected-dialog facts above.

## As built (2026-10-13)

Commits `380cdba` (matcher split), `412f755` (the cap), `db1d1c6` (docs), `c1d6ac7` (final-review fixes). Where the build differs from the design above:

- **A wrong fact, corrected.** The Facts section says a plugin's own `$.prompt.submit` runs through the mod's `prompt.submit` hook. It does not: live, every mod-raised submit is logged `hooks module switchboard@switchboard prompt.submit skipped: re-entry (the plugin's own code raised it; origin switchboard)` (170 times in r5, the log this spec cites; its "settled" lines were typed prompts). So the reset rule "any `prompt.submit` other than the mod's own bare redirect (origin plus exact text)" never saw the mod's own submits live. The build resets in `submitPrompt` whenever a submit carries held phone messages (a bare redirect keeps the count), and `prompt.submit` resets only for prompts from elsewhere, recognised by origin alone (`isOwnSubmit`, `plugin/hooks/turn-end.ts`); the exact-text match is gone. The test kit gets this backwards (a submit raised inside a hook reaches `prompt.submit` there), which is why the kit passed with the original rule.
- **The mod's own `$.mcp.call` is now treated as its own.** The cap notice runs through the mod's `tool.call` hook, which posted a tool status and then `thinking` after the turn's `clear`, so the idle session showed as active on every surface (seen live by John and by the final review), and the hook could attach held phone messages to a result only the mod reads. The hook now recognises the mod's own call by `next.origin.plugin === 'switchboard'` (`isOwnCall`, `plugin/hooks/status.ts`): it never takes held messages, and with no turn running it posts no status. The AskUserQuestion bridge, whose calls run inside a turn, keeps its statuses.
- **An answered `AskUserQuestion` also resets the count** (made at planning): the bridge's `$.mcp.call` passes through `tool.call` live but not in the kit, so the reset is explicit (`askedJohn`), and a terminal-answered question is John's input too.
- **`turnEndBlock` is gone**, replaced by `redirectPlan`, which also moves the count past the cap (to 3), so a later turn end in the same chain stays quiet. The cap notice reads "ended 3 turns" from `REDIRECT_CAP + 1`.
- **Verification.** pytest 1353 passed (one pre-existing httpx DeprecationWarning); `claude plugin test plugin` 141 pass; typecheck clean; `claude plugin validate` passes with the shipped warnings. Live on `db1d1c6`, away mode on with John, in `default`-mode haiku sessions:
  - **The cap:** a session told to answer in text ended exactly three turns, then sent the notice (auto-allowed in 374 ms, no approval card, no dialog drawn), and stayed idle; John's phone reply started a new turn, then two redirects and a second notice.
  - **`ask_human`:** the inline allow answered it, the question card posted with no approval, and John's answer came back.
  - **Bash:** its approval card still posted, and John's Allow from the phone ran the command.
  - **The own-call fix** (at the desk, with a `--plugin-dir` copy of `c1d6ac7` plus a probe trigger): the mod's own `notify_human` reached `tool.call` with origin `switchboard` and no running turn, and no `/agent_status` post followed the turn's `Stop`.
- **Deferred minors** (final review): the count's read-then-write in `checkTurnEnd` is not atomic; `^mcp__switchboard__` also matches a server named `switchboard__<x>`; the inline hook's 10 s timeout sits at the Git Bash startup floor.
