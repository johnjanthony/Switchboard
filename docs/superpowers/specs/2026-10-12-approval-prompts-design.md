# Approval prompts on the phone: show a permission dialog there, and answer it (design)

**Date:** 2026-10-12
**Status:** written 2026-10-12 while John was away, from the decisions he took after the probe report ("proceed with your recommendations"); awaiting his review of this written spec. Nothing is implemented.
**Backlog:** T-284 (approval prompts through the mod). It brings back, from a new source, the approval signal T-279 retired (`blocked_on_approval`, removed in `b2fd4ee`, `c5cd438` and `12b52da`).
**Evidence:** the probe of 2026-10-12 on Claude Code 2.1.296 (Windows terminal; four headless runs, two of them in `auto` mode, and nine interactive runs in `default`-mode sessions driven through a ConPTY), written up in the vault note `Reference/Claude Code Permission Prompts from a Plugin`, with its plugin, driver and logs in `C:\Work\Claude-Scratch\T-284-perm-probe\`; the 2.1.296 mod declarations; and the code paths cited below.

## Goal

When a Claude Code session opens a permission dialog ("Do you want to proceed?"), the session stops until someone answers it at the terminal. With John away, that stop is silent: nothing reaches the phone, and since T-279 no roster shows it either.

This work makes a dialog visible and answerable away from the desk:

1. **Every open dialog marks its session "needs approval"** on the phone's sessions board, in Operator and in Watchtower, at the desk or away.
2. **In away mode each dialog becomes a card on the phone** (and in Operator) with Allow and Deny. The answer goes back to the dialog, which closes as if answered at the terminal.
3. **The terminal keeps working.** Whichever answers first wins, and the card withdraws itself when the terminal answers.

It matters once sessions run in `default` or `auto` mode; `bypassPermissions` never opens a dialog.

## Facts the design rests on (probed 2026-10-12, Claude Code 2.1.296)

- **A plugin's `PermissionRequest` command hook runs under the org's security plugin, and its answer is obeyed.** The hook lives in the plugin's `hooks.json` beside `"modules"`. Its output `{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}` (or `"deny"` with a `message`) settled the call (`returned permissionDecision: allow`). A mod's own `classic.PermissionRequest` hook is bypassed (`bypassed by cc-plugin-sec-default (tier user); beneath runs`); the command hook is the "beneath".
- **The hook fires exactly when the dialog opens, and only then.** `executePermissionRequestHooks` ran in the same millisecond the dialog was drawn, and only for calls that prompted.
- **The dialog and the hook race; the first answer wins.** The dialog draws at once while the hook runs. A hook `allow` 15 s and 150 s later dismissed the open dialog (the transcript row reads "Allowed by PermissionRequest hook"). A hook `timeout` of 86400 was accepted, and a hook is not moved to the background at 120 s.
- **A selection at the terminal does not stop the hook.** After "Yes" at 5 s of a 60 s hold, the hook ran on; its `deny` at 60 s was parsed and ignored. **Esc does stop it** (`cancelled`), and **so does its own timeout** (the dialog then stays open).
- **The hook's input** carries `session_id`, `cwd`, `tool_name`, `tool_input`, `permission_mode`, `permission_suggestions`, and `agent_id` inside a subagent. It has **no `tool_use_id`**. Its `tool_input` equals the mod `tool.call` hook's arguments exactly (canonical JSON; Bash and Write checked).
- **`PermissionRequest` also fires for AskUserQuestion's question dialog** (`tool_name: "AskUserQuestion"`).
- **Plugin command hooks run under Git Bash on Windows** (`BASH_VERSION` 5.3.9, `$0` `/usr/bin/bash`), as they do under bash in WSL.
- **The mod sees each decision as it is made.** A `telemetry.log` hook matching `{ to: 'collector', event: 'tool_decision' }` receives the engine's OpenTelemetry record the moment a call is decided, before the tool runs: `tool_use_id`, `tool_name`, `decision` (`accept` / `reject`) and `source`: `user_temporary` (terminal Yes), `user_reject` (terminal No, and Esc), `hook` (a `PermissionRequest` hook), `config` (a rule or the mode). It arrives with no OTel environment set. A telemetry hook on the `anthropic` stream does not load.
- **`$.tool.check` from a `tool.call` hook works** under the security plugin (`ask`, 13-41 ms per call), but its doc says it consults no PreToolUse hook or auto-mode classifier, so it only predicts a dialog.
- **The `permission_prompt` notification is late:** it fires about 6 s after the dialog opens, and only if the dialog is still open by then.
- **A mod's own `tool.check` hook ran, and its verdict landed** (headless: `ask -> allow by plugins cc-plugin-sec-default, sb-perm-probe`), while the security plugin's own log line called it bypassed.
- **The phone's and Operator's reply chips come from `conversations/<conv>/pending_questions/<request_id>`** (its `suggestions`); an answer is written to `answers/<conv>/<request_id>` as `{text, sender, request_id, written_at}`, and the server routes it in `dispatch_responses` (`server/gateway/dispatch.py:21`). A card is withdrawn by `mark_question_cancelled` (`server/firebase.py:163`), which also clears its push notification and its `pending_questions` record. The per-conversation `pending_responses` counter is driven by `Registry`'s pending mirror (`server/main.py:928-931`).
- **A question message pushes even at the desk** (`write_conversation_message`'s at-desk gate allowlists `question`, `server/firebase.py:924-931`).

## Decisions (settled with John, 2026-10-12)

1. **Design for `default` and `auto` modes.** `auto` is probed at planning: whether the hook fires before the classifier or only when the classifier hands a call to the dialog. Either way the design holds, since it keys on the hook, which fires only when a dialog opens.
2. **The hook always holds.** Every dialog's hook waits on the server, at the desk too. The card shows only in away mode, and follows away mode as it turns on and off. Rejected: holding only in away mode, which strands a dialog already open when John walks away.
3. **The card rides the existing question machinery,** as a question message with Allow and Deny suggestions and a `pending_questions` record, in the session's conversation. So the phone, Operator, push and the answer path need no new UI. Refined while grounding (see Rejected alternatives): the server keeps approvals in their own small registry rather than as a third slot in `Registry`, and `dispatch_responses` gains one branch for them.
4. **No "always allow" from the phone.** Allow and Deny only; the hook never returns `updatedPermissions`.
5. **The hook is a bash script using curl.** One script serves Git Bash on Windows and bash in WSL; no Python.
6. **Surfaces: terminal sessions on Windows and in WSL.** Desktop-app sessions are a live check in this chunk. If the app draws its own permission UI that a hook answer does not close, that is recorded as a limitation and filed.

## Design

### 1. The flow

```
dialog opens ──► PermissionRequest hook (permission-request.sh)
                   POST /approvals  (holds until decided or released)
                        │
                        ├─ server opens an Approval; the session record shows "needs approval"
                        ├─ away mode on ──► card in the session's conversation (+ push)
                        │
   phone answers ───────┤──► 200 {decision} ──► hook prints it ──► dialog closes
   terminal answers ────┤    (the mod's tool_decision report) ──► 204 ──► hook exits quietly
   Esc / timeout / exit ┘    (the hook dies; the server sees the disconnect) ──► approval closed
```

### 2. The hook (`plugin/scripts/permission-request.sh`)

`plugin/hooks/hooks.json` gains a `"hooks"` block beside `"modules"`:

```json
"hooks": {
	"PermissionRequest": [
		{ "hooks": [ { "type": "command", "command": "bash \"${CLAUDE_PLUGIN_ROOT}/scripts/permission-request.sh\"", "timeout": 86400 } ] }
	]
}
```

The script:

- Reads the whole hook input from stdin once, and keeps it so it can resend it.
- POSTs it with curl to `${SWITCHBOARD_BASE_URL:-http://127.0.0.1:9876}/approvals`, adding `Authorization: Bearer $SWITCHBOARD_TOKEN` when that variable is set (as the mod does), with no client-side time limit. The connection is the hold.
- **200:** prints the body (the hook JSON) to stdout and exits 0.
- **204, or any 4xx:** prints nothing and exits 0, so the dialog stays with the terminal.
- **503, or no connection** (refused, reset, empty reply): retries with backoff (2 s, then 5 s), for up to 10 minutes of continuous failure, then exits 0 with nothing. This rides out a service restart, after which the resent input opens a fresh approval.
- **Always exits 0, and prints nothing but a decision.** A non-zero exit from a `PermissionRequest` hook must never be possible; in particular exit 2, which Claude Code treats as a blocking error.

`timeout: 86400` bounds the hold to 24 hours; when it fires, Claude Code kills the script, and the server sees the disconnect.

The rule that already governs hook scripts applies to this one: never rename or remove it while sessions that loaded the old `hooks.json` still run.

### 3. The server

**Approval registry** (new module, `server/approvals.py`). It holds the open approvals in memory:

- `id`: a fresh request id, which is also the card's `request_id`.
- `cli_session_id`, `agent_id` (None on the main loop), `tool`, `tool_input` and its canonical digest (`json.dumps(input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` hashed with SHA-256).
- `cwd`, `opened_at`.
- The decision future the route awaits.
- The card: `conversation_id` and `msg_id` while one is posted, else None.

It is not part of `Registry`. The ask machinery (blocking and background slots, supersession, parking, the away-exit drain over `all_pending()`, the free-form message ladder) never sees an approval. The approval registry drives `Registry`'s pending mirror for the conversations its cards sit in, through a public `Registry` method wrapping `_fire_pending_mirror`, so the `pending_responses` counter and the reply input behave as for an ask.

**`POST /approvals`** (the hook's long-poll; the Bearer middleware applies, loopback exempt):

1. It parses the hook input. A body without a `session_id` string or `tool_name` gets 204.
2. If `tool_name` is `AskUserQuestion` or `ExitPlanMode` it answers 204 at once. The mod's bridge owns AskUserQuestion, and plan approval is a different decision from a permission (see Out of scope).
3. It opens an approval, touches the session record (creating it if unknown, as other hook routes do), and marks the session awaiting approval (section 5).
4. If away mode is on, it posts the card (section 4).
5. It awaits whichever comes first: the decision future, or the client disconnecting (watched by reading the ASGI `receive` channel for `http.disconnect`).
   - **Decided:** 200 with the decision JSON.
   - **Released** (terminal answer, session end, turn-end backstop): 204.
   - **Client gone:** close the approval and return nothing.
   - **Service stopping** (the `ServiceStop` flag raised at `handle_exit`): 503, so the script retries after the restart.

Every way an approval closes runs one function, which:
- withdraws its card if one is posted (`mark_question_cancelled`, pending mirror -1);
- clears the session's awaiting-approval mark when it was the session's last open approval;
- settles the future;
- logs one JSONL line (`approval_closed`, with the reason).

**`POST /sessions/{sid}/approvals/settled`** (the mod's report, section 6). Body: `{tool_use_id, tool, agent_id, input, decision, source}`.
- It matches the open approval with the same session, agent, tool and input digest. When there is no exact match, it falls back to the oldest open approval with the same session, agent and tool.
- It releases the match with 204 to the hook and logs `approval_settled_at_terminal`.
- No match is not an error (the phone may have answered first). It always answers 200, the hook-route contract.

**Backstops.**
- **Session end** (`handle_session_end`) closes all of the session's approvals.
- **A main-loop turn end** (the `Stop` status post in `_build_agent_status_route`) closes the session's main-loop approvals (`agent_id` None), since a main-loop dialog cannot outlive its turn. This covers a terminal answer whose report was lost.
- **A disconnect** covers Esc, the hook's timeout and the process exiting.

**Away mode.** `Registry` gains an away-mode transition observer, called on every change of the flag, whether it comes from the setter or from the Firebase listener's `update_global_away_cache`.
- **Turning on:** posts cards for every open approval without one.
- **Turning off:** withdraws every posted card. The approvals stay open and their hooks keep holding.

The away-exit drain in `dispatch_away_mode_commands` is untouched, since it walks `Registry` only.

**Answers.** `dispatch_responses` first asks the approval registry whether the answer's `(conversation_id, request_id)` is one of its cards.
- **If so:** it maps the reply text to a decision (section 4), resolves the future, and writes the reply into the conversation history attached to the card, as for an ask.
- **Otherwise:** the existing path runs unchanged.

An answer to an approval already closed is a benign replay: logged, and its slot deleted, with no "reply withdrawn" notice. The approval registry keeps its own bounded memory of recently closed ids for this.

**Restart.** Approvals are in memory. On restart every hook's connection drops, its script retries, and the resent input opens a fresh approval (and card, in away mode). Hydration cancels any `pending_questions` record flagged `approval: true` (`mark_question_cancelled`) instead of parking it, so a stale card from before the restart withdraws.

### 4. The card

The card is posted with `write_conversation_message(conv, sender, "question", text, request_id=approval.id, suggestions=["Allow", "Deny"], title=f"Approve {tool}?", format="markdown")`. A `pending_questions` record goes with it: the usual fields plus `approval: true`. Where it goes:

- **Conversation:** the session's own, resolved as `ask_human` resolves it (`_resolve_conversation_and_member`, which mints a single-agent conversation for an unbound session).
- **Sender:** the session's canonical member name, or `Claude` when one is minted.

The ask rate limiter does not apply: dialogs are paced by the engine and by John's answers, not by the agent.

The card's text names the tool and shows what it will do:

- **Bash:** the command in a `sh` code block, and the model's `description` beneath.
- **Write:** the path and line count, and the first 20 lines in a code block.
- **Edit:** the path, then `old_string` and `new_string` in code blocks, each cut at 1,500 characters.
- **Any other tool, an MCP tool included:** the input as indented JSON, cut at 1,500 characters.
- **In a subagent:** "in a subagent" after the tool name.
- **Last line:** "Reply Allow or Deny, or tell Claude what to do instead."

**Reply mapping**, case-insensitive and trimmed:

| Reply | Decision |
|---|---|
| `allow`, `yes`, `y`, `approve`, `approved`, `ok`, `okay` | `allow` |
| `deny`, `no`, `n` | `deny`, message "John denied this from his phone." |
| anything else | `deny`, message = John's text |

A deny never sets `interrupt`: the model reads the message and carries on, which in away mode means asking John through `ask_human`. A terminal "No" interrupts the turn, which would leave an away session idle.

### 5. Needs-you surfaces

- **Session record:** two fields on `SessionRecord`, mirrored to `sessions/<sid>`.
  - `awaiting_approval` (bool): true while the session has an open approval.
  - `approval_since` (ISO time of its oldest open approval, else null).

  The tool detail is already on the record as `state_detail` (the `PreToolUse` status post).
- **`/stats` `needs_you`** gains the reason `approval` (age from `approval_since`) for live sessions awaiting approval. `ask` wins when a session has both, as before `b2fd4ee`. Watchtower needs no change: it keys only on presence (`DetailPanel.cs:591-592`, `:961-962`), which lights the session's amber dot and sorts it first.
- **Phone:** `SessionBoardPolicy.sessionNeedsAttention` returns true for `awaitingApproval`, ahead of the idle rule. The session row shows the "needs approval" chip. Both are restored from `c5cd438` with the new field; the 5-minute "possibly waiting on approval" hint stays gone.
- **Operator:** `needsAttention` and `sessionChip` likewise, restored from `12b52da` with the new field.

### 6. The mod

The mod changes in two places:

- **`tool.call` remembers the call in flight.** Before `next(call)`, a module-level map gets `tool_use_id → {tool, agentId, input}`, where `input` is the call's arguments as passed to `next` (after the switchboard injector's rewrite, since that is what the permission check and the hook see). The entry goes when `next` settles.
- **A telemetry hook reports terminal answers.** `on('telemetry.log', { to: 'collector', event: 'tool_decision' }, ...)` observes only and always returns `next(e)`. When `source` starts with `user` and the `tool_use_id` is in the map, it posts `{tool_use_id, tool, agent_id, input, decision, source}` to `/sessions/{sid}/approvals/settled`, fire-and-forget like the status posts. Other sources (`hook`, `config`, the classifier's) post nothing, so only a decision a person made at the terminal makes traffic.

The mod does not call `$.tool.check`; the hook's arrival is the exact signal.

### 7. Error handling

- **Server down:** the script retries for up to 10 minutes and then gives up quietly. The dialog was never blocked, since it draws at once.
- **401 from WSL** (a missing or wrong token): the script exits 0 with no decision. The mod already logs its own 401 once per session, so the transcript names the cause.
- **A posted card whose write fails:** the approval stays open without a card (logged); the next away-mode transition retries the post.
- **An answer racing a terminal answer:** whichever reaches Claude Code first wins. The loser is ignored by Claude Code (the hook's late answer) or by the server (an approval already closed).
- **Several dialogs at once** (parallel subagents): each hook holds its own connection and gets its own approval and card. The settled report matches on agent and input.

## Rejected alternatives

- **Answering through the mod's own `tool.check` hook.** Its verdict landed in the probe, but the security plugin logs it as bypassed, so it looks like a gap that may close, and leaning on it routes around an org control. It would also hold the call before the dialog draws.
- **Approvals as a third slot in `Registry`.** Every accessor, the away-exit drain over `all_pending()`, the free-form message ladder, parking and hydration would need an approval case, and a missed one could, for example, answer a dialog with the away-exit bulk text. A separate registry plus one dispatch branch keeps the ask paths untouched.
- **`$.tool.check` as the detection signal.** It is a prediction (no PreToolUse hook or classifier) and costs 13-41 ms on every tool call.
- **The `permission_prompt` notification** as the detection signal: about 6 s late, and only for a dialog still open.
- **A Python hook script:** an interpreter dependency on both hosts (and bare `python` opens a REPL in Git Bash). **`"type": "http"` hooks:** unverified on this build, and they need a URL and token per host.
- **"Always allow" from the phone:** writing permission rules remotely is the riskier half; Allow and Deny cover the need.
- **A card at the desk:** at the desk the terminal is in front of John, and every prompting session would gain a conversation.

## Out of scope

- **ExitPlanMode's plan approval.** Its dialog chooses how to leave plan mode, which an allow or deny does not express. Filed as a follow-up.
- **A dedicated approval UI** (buttons, a diff view). The question card is the first cut.
- **The mod fingerprint covering `plugin/scripts/`.** The stale-mod alarm watches `plugin/hooks/*.ts` only; a desktop copy with an old script is caught by `deploy-mod.ps1`'s comparison.

## Testing

- **Server (pytest):**
  - **Approval registry lifecycle:** open; answer each way; release by settled report (exact and fallback match); disconnect; session end; turn end; away on and off (cards posted and withdrawn, pending mirror +1/-1).
  - **The `/approvals` route through Starlette's test client:** 200 with the decision; 204 on release; 204 at once for AskUserQuestion and ExitPlanMode; 503 when stopping; a disconnect closes the approval.
  - **Other paths:** the `dispatch_responses` branch (with a replay); hydration cancelling `approval: true` records; card rendering per tool; the reply mapping; `needs_you` and the session record fields.
- **The hook script (pytest driving `bash`** against a small local HTTP server): 200 prints the body; 204 and 401 print nothing; it retries through 503 and a refused connection; it exits 0 in every case.
- **The mod (`claude plugin test`):** the in-flight map; a `user_*` `tool_decision` posts the settled report and a `hook` or `config` one does not. Whether the test kit can raise `telemetry.log` is checked at planning.
- **Android:** `SessionBoardPolicy` unit tests for the restored attention and chip.
- **Operator:** `derive.test.js` likewise.
- **Live checks:** the ConPTY driver from the probe, against the deployed service.
  - **At the desk:** a dialog marks the session "needs approval" and the terminal answer clears it.
  - **Away** (John-gated, since away mode is global and pushes to his phone): the card appears; Allow from the phone runs the command; Deny delivers the message; a terminal answer withdraws the card; Esc closes it; toggling away mode posts and withdraws cards; a service restart reposts the card.
  - **Desktop app:** one dialog, to see whether a hook answer closes the app's permission UI.
  - **WSL:** one dialog, through the Bearer gate.
