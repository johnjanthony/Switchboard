---
name: switchboard
description: Reach John via the ask_human, notify_human, and send_document_human MCP tools ONLY when away mode is on. When John is at his desk, report through the terminal directly — sub-agents return via the Task tool to the controller, not via switchboard. When in away mode, invoke ask_human for decisions that would otherwise stall (file overwrite, migration, ambiguous intent, permission to proceed); invoke notify_human for non-blocking status updates. The server's at-desk redirect ("ERROR: John is at his desk..." plus a passive notification) is a safety net for missed gating, not the primary path — a call landing in it means the trigger condition was wrong.
---

# CRITICAL: Away Mode Protocol

**If John has stepped away, YOU MUST NOT PRODUCE ANY TEXT OUTPUT IN THE TERMINAL.**

This is the single most important rule. If you produce chat text in the terminal after John has left, you have failed the protocol. Intercept your instinct to acknowledge tasks ("Okay", "On it", "Done") or report status in the terminal.

**The tool call IS the acknowledgment.**

**Away mode is user-managed.** John controls the away-mode flag himself (phone app, etc.). The agent only toggles it in response to **an explicit signal in John's MOST RECENT prompt** — never on conversation history, never on hook prompts, never by inference.

- "I'm stepping away" (or equivalent) in the current turn → call `set_away_mode(true)`.
- "I'm back at my desk" (or equivalent) in the current turn → call `set_away_mode(false)`.
- Anything else, including the same phrases said earlier in the conversation, is not authorization. If John said "I'm back" three turns ago, that has no bearing on this turn — assume he has already managed the state himself.

Spawned sessions auto-enter away mode on spawn (the phone auto-enables it) — do not call `set_away_mode(true)` redundantly at spawn time.

**If the turn-end hook blocks you:** If you see an unexpected `block` (Claude) or `deny` (Gemini) message from a turn-end hook injecting "You are in away mode...", the flag is still active server-side. The hook's "call set_away_mode" prompt is **not** authorization to flip the flag — only the user's MOST RECENT prompt is. If this turn's user message contains the explicit back-at-desk signal, call `set_away_mode(false)` first and then resume terminal output. Otherwise, route this turn's output through `ask_human` or `notify_human` and leave the flag alone.

1. **Entry:** When John's MOST RECENT prompt says "stepping away", "going away mode", "I'm away", or any equivalent phrasing — even when bundled with other directives in the same message — the trigger fires. Earlier-in-conversation mentions do not count.
   - **`set_away_mode(true)` MUST be your first tool call**, before any other work the message asks you to do. The flag must be set server-side so that subsequent `ask_human` calls block normally instead of triggering the at-desk redirect.
   - After the flag is set, then continue:
     - If tasks are queued: call `notify_human` to confirm you are starting, then begin work.
     - If idle: call `ask_human` to ask what's next.
     - If mid-task: call `notify_human` to report status, then `ask_human` for next steps.
   - For spawned sessions, the flag is already set at spawn time — skip the `set_away_mode(true)` call.
   - **Zero terminal text.** A compound user message like "I'm stepping away. Call send_document_human with foo.txt, then ask_human about delivery." is NOT a license to skip `set_away_mode(true)` and dive into the explicit commands. Set the flag first, then do the work.
2. **Execution:** Route **every** status update, question, or completion ping through `notify_human`, `ask_human` or `send_document_human`.
3. **Replies:** When `ask_human` returns a reply, do not acknowledge it in the terminal. Treat it as input for your next tool call or task.
4. **Exit:** The **only** exit trigger is John's MOST RECENT prompt explicitly saying he is back ("I'm back", "back at desk"). Call `set_away_mode(false)` as your first action, then resume terminal chat with a concise summary of your work. A back-at-desk message from earlier in the conversation does not retroactively authorize an exit on a later turn.

**At-desk `ask_human` redirect.** If `ask_human` returns the literal string `"ERROR: John is at his desk. Ask this question via the terminal."`, the human is at their desk, and your question has already been delivered to their phone as a passive notification (chat history + Updates channel, no reply UI). Your next action is to **produce the question content verbatim in the terminal** — the human will respond via terminal input. Do NOT retry `ask_human`, do NOT call `set_away_mode(false)` (the flag is already off), and do NOT treat this as an error worth surfacing beyond asking the question. This redirect only applies to `ask_human`; `notify_human` and `send_document_human` always deliver normally regardless of away-mode state.

---

# Switchboard MCP Tools

Switchboard is a local MCP gateway that lets you reach John on his phone while he's away from his desk. The plugin's `cli-session-injector-hook.py` PreToolUse hook automatically injects `cli_session_id` and `cwd` into every switchboard tool call — you only pass `sender` and the tool's own arguments.

**Active tools:**

- **`ask_human(question, sender, title?, format?, suggestions?)`** — blocks until John replies. Returns reply text or `"__TIMEOUT__"`.
- **`notify_human(message, sender, title?, format?)`** — fire-and-forget. Returns `"ok"`.
- **`send_document_human(path, sender, title?, caption?)`** — deliver a file. path relative to your cwd or absolute. Max 5 MB. Returns `"ok"` or `"ERROR: ..."`.
- **`message_and_await_agent(sender, message, title?)`** — conversations only. `message` is required and non-empty. Send to peers and block until woken. Returns the conversation log since your last wake (excluding your own emissions). **Sole-alive behavior depends on whether the conv is the open marker:**
  - **Open-marker conv (lobby-hold):** blocks like the mint-path `open_conversation` — waits for the next peer to join, then returns `"ok. open_conversation = <id>\nPeer '<name>' joined."`. On timeout returns `"__TIMEOUT__"` but leaves the conv alive so you can poll again or `leave_conversation` explicitly.
  - **Non-open conv:** returns `"__CONVERSATION_EMPTY__"` immediately AND auto-removes you from the conv (mirroring `leave_conversation` — the agent's "I was removed" belief matches reality). The conv transitions to Ended if no members remain.
- **`open_conversation(sender, title?)`** — promote your current conversation to be the global `openConversation` pointer so other agents can self-join via `enter_conversation()`. **Two behaviors depending on whether you're already in a conversation:**
  - **Bound caller (promote):** non-blocking. Flips the open marker to your current conv and returns `"ok. open_conversation = <id>"` immediately.
  - **Unbound caller (mint):** mints a fresh conversation, promotes it, then **blocks** until a peer joins via `enter_conversation()` or times out. On wake, returns `"ok. open_conversation = <id>\nPeer '<name>' joined."` On timeout, the conv is force-ended, the open marker is cleared, and you get `"__TIMEOUT__"` — so failed bootstraps don't leak orphan conversations. After the wake, call `message_and_await_agent` to greet the joiner (their `enter_conversation` is still blocked in the intro queue until someone speaks).
- **`enter_conversation(sender)`** — unified "join + listen for intro". Blocking. Queues you in your conversation's wait queue; returns the conversation log when the next peer speaks (full history if you just joined, delta since your last wake if already a member). See branching behavior below.
- **`combine_conversations(source_id, target_id)`** — move all members of `source_id` into `target_id`; source ends. Non-blocking.
- **`lookup_conversation_ids(cwd_filter?, sender_contains?, title_contains?)`** — find conversation_ids matching filters. At least one filter required. Returns a JSON list.
- **`leave_conversation(sender, parting_message)`** — leave your current conversation. `parting_message` is required. Session falls back to home conversation (away on) or unbound terminal output (away off).
- **`set_away_mode(value)`** — flip the global away-mode flag to `true` or `false`. Persisted to Firebase.

**Retired tools (do not call):**

- `end_collab` — subsumed by `leave_conversation`.
- `enter_away_mode(cwd)` / `exit_away_mode(cwd)` — replaced by `set_away_mode(bool)`.

## Your `sender`

`sender` is your display name in the conversation and on John's phone. Pick a **short, unique, human-readable name** — natural casing is fine and reads better than identifier-style names on the phone. Surface labels like `Claude Win`, `Claude WSL`, or `Gemini` work; role labels like `Reviewer`, `Implementer`, or `Architect` are often clearer in multi-agent collabs. If John named you in your spawn prompt, use that name. Distinctness matters when multiple agents share a conversation: John sees names on bubble attributions; peers see names in message payloads. If you pick a name another member already holds, the server appends a numeric suffix (e.g. `Claude Win 2`).

`sender` is **required** on every tool call — omitting it raises a schema error. There is no default.

Within a single conversation, **no two members need unique senders by rule**, but collision produces confusing attributions — avoid it. If you are being spawned into an existing conversation, the spawn prompt includes the current member roster; pick a name that doesn't collide with it.

## Your `title`

`title` is your session label — displayed as the conversation tab name on John's phone. It's optional on every messaging tool, but **first call must set one**.

**First call in a fresh session:** synthesize from your task. If you've been doing meaningful work already, use a noun phrase or verb-ing form like `"Reviewing PR #1234"` or `"Fixing flaky FCM tests"`. If you're brand new with no task underway, use the leaf folder name from your cwd (`c:/work/switchboard` → `"Switchboard"`).

**Subsequent calls:** Omit `title` unless your scope has materially changed. The server treats omitted title as "no change." Don't repeat the same title every call — that's just noise. Update when you genuinely shift focus (e.g. `"Reviewing PR #1234"` → `"Implementing PR #1234 review feedback"`).

**Constraints:**
- Length: ≤80 chars (server truncates excess).
- Style: noun phrase or verb-ing form. No trailing punctuation.

## Response conventions

- Be concise in questions. John is on his phone. One or two sentences.
- Include enough context that John can decide without opening his laptop. Include file paths, commit IDs, or the specific ambiguity you need resolved.
- Suggest a default when there is one: "Overwrite foo.java with the refactored version? (default: yes)".
- For multi-choice, put the options in the question: "Use ActiveMQ or Kafka for the new event bus?"

## Suggestion buttons

`ask_human` accepts an optional `suggestions` list. When provided, the client renders tap-able inline buttons below the question — John taps a button and its label is returned as the response string.

```
ask_human("Overwrite foo.java?", sender="Claude Win", suggestions=["yes", "no", "abort"])
# → returns "yes", "no", or "abort", or a typed free-text reply
```

Use suggestions for binary or small-choice decisions where tapping beats typing on mobile. Keep suggestion labels short (under 64 characters each). When suggestions are present, John can still type a free-text reply if they want to say something other than the suggestions.

## Formatting messages

Both tools accept an optional `format` parameter: `"plain"` (default) or `"markdown"`.

When `format="markdown"`, the Android client renders the message using Markdown. Use standard Markdown syntax.

**Supported syntax:**
- `**bold**` — emphasis, headers
- `_italic_` — secondary info
- `` `inline code` `` — file paths, variable names, values; renders as cyan monospace
- ` ```code block``` ` — multi-line code or command output; preserves line breaks
- `[link](url)` — tappable links
- `- [ ]` and `- [x]` — checklists
- `| Table |` — Markdown tables

**Example of a well-formatted status message:**

```
ask_human(
  "**Migration complete**\n\n"
  "Processed `CustomerMapper.java` — 3 methods rewritten.\n\n"
  "```\nPASS  CustomerMapperTest (4/4)\nPASS  IntegrationTest (12/12)\n```\n\n"
  "Ready to commit. Proceed?",
  sender="Claude Win",
  format="markdown",
  suggestions=["yes", "no"]
)
```

Use `format="markdown"` when the message contains structure that benefits from formatting: status summaries with code snippets, file paths, multi-line output. Keep plain-text messages as `format="plain"` — don't wrap simple one-liners in Markdown.

## Handling `"__TIMEOUT__"`

If `ask_human` returns `"__TIMEOUT__"`, John did not reply within the window. Do not guess and continue. Instead:

1. Record what you were about to do and why you needed input.
2. Pause the current work stream. Do not take irreversible actions.
3. When John returns, resume from where you paused.

Use `notify_human` to record the pause if it is helpful context for later: `notify_human("Paused DMXRefactor — timed out waiting on approval to overwrite CustomerMapper.java", sender="Claude Win")`.

## Handling `"ERROR: ..."`

If `ask_human` returns a string starting with `"ERROR:"`, the gateway itself failed. Treat this the same as a timeout — pause, do not guess. If possible, use a shell command to check if the Switchboard server process is still running (e.g., `netstat -ano | findstr :9876`) to diagnose the failure before pausing.

**Exception:** the literal string `"ERROR: John is at his desk. Ask this question via the terminal."` is NOT a gateway failure — it is the at-desk redirect described in the Away Mode Protocol above. In that specific case, produce the question in the terminal and continue; do not pause.

## Staying alive in away mode

While in away mode, after completing a discrete task **that John handed to you** (not merely an intermediate step within that task — not running tests, not reading files, not committing), call:

```
ask_human("Task done: <one-line summary>. What's next?", sender="Claude Win")
```

instead of ending your turn. This keeps the session alive so John can queue additional work from his phone without needing to re-spawn you.

Treat `"__TIMEOUT__"` as permission to end the session gracefully.

The "discrete task John handed to you" phrasing is load-bearing — do not ping between internal subtasks.

## Sending files with `send_document_human`

Use this to deliver generated reports, diffs, logs, or spec documents to John's phone for review. It is fire-and-forget — the agent does not block waiting for a reply. Per the "never end on fire-and-forget" rule, there must always be at least one `ask_human` after any `send_document_human` call.

**Constraints enforced by the gateway (violations return `"ERROR: ..."`):**

- `path` may be **absolute** or **relative**. Relative paths are resolved against your cwd; `..` traversal that escapes the project root is rejected. Absolute paths are accepted as-is.
- Maximum file size: **5 MB**.
- Denied filenames: `.env`, `service-account.json` (exact match), and anything matching `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env` (case-insensitive glob — covers `.env.local`, `.envrc`, `prod.env`, etc.).
- The gateway logs the resolved path, file size, and SHA-256 hash of every delivered file.

**`caption`** is optional (max 1024 characters). Use it to give John context: `"Migration diff — 47 tables affected"`.

**Example:**
```
send_document_human("logs/migration-diff.txt", sender="Claude Win", caption="Schema diff for review")
```

---

# Conversations

Conversations are the routing and persistence unit. A conversation is identified by a server-minted UUID. Every switchboard tool call is routed through your conversation via the hook-injected `cli_session_id` — you never pass a cwd or channel key.

## Lifecycle states

- **Active** — has at least one member (alive or dormant). The normal operating state.
- **Ended** — terminal. No members remain. Persists in Firebase as history.

At most one Active conversation is "the open one" at any time — set by `open_conversation()` and joinable via `enter_conversation()`.

## Member states

- **Alive** — your CLI session is bound; you are running and responsive.
- **Dormant** — your CLI session exited (SessionEnd hook fired); you are retained in the conversation for revival via resume or combine. The conversation stays Active.
- **Permanently lost** — your session exited via `clear` or `compact` (unrecoverable). Non-resumable.

## Session-fallback rule

When you leave a conversation (via `leave_conversation`, force-end, or combine-out), you are never orphaned:

- **Away mode on:** your session re-binds to your home conversation (the conversation you were first bound to). If the home is Ended, the server creates a new Active conversation for you.
- **Away mode off:** your session becomes unbound. Subsequent `ask_human` / `notify_human` calls get at-desk-redirected; your output reaches John via the terminal.

## Sentinels

- **`__CONVERSATION_EMPTY__`** — returned by `message_and_await_agent` when you are the sole alive member. You have been removed per the session-fallback rule; the conversation may have ended. End your turn or report to John.
- **`__CONVERSATION_ENDED__`** — returned when John force-ends the conversation. Same exit protocol as `__CONVERSATION_EMPTY__`.

## `enter_conversation` branching behavior

`enter_conversation(sender)` is the "join + listen for intro" tool. It blocks until a peer speaks. Behavior depends on your current state:

1. **Already in a conversation, no open conversation (or open = yours):** queues you in your current conversation's wait queue without writing a speak event. Blocks until a peer speaks. Use this when you've just been moved into a conversation (via combine/spawn-into-existing) and want to receive context before introducing yourself.
2. **Not in any conversation + open conversation exists:** you are added to the open conversation as a new member; queued in its wait queue; blocks for intro.
3. **In conversation X + open conversation Y (X ≠ Y):** you migrate from X to Y (X's session-fallback rule applies to your departure); added to Y; queued; blocks for intro.
4. **Not in any conversation + no open conversation:** returns `"ERROR: no open conversation. Ask John to open one on the phone, or have an agent already in a conversation call open_conversation."`.
5. **Already in a conversation + no open conversation:** queues you in your current conversation's wait queue without migration (same as case 1).

When woken: returns full conversation history if you just joined as a new member; returns delta since your `last_seen_seq` if you were already a member.

---

# Collab composition patterns

Three patterns for putting multiple agents into one conversation:

## Open + Enter

Agent A calls `open_conversation(sender, title)` to promote their conversation to the open singleton. Other agents call `enter_conversation(sender)` to migrate in. Use this when John tells one agent to start a collab session and tells others to join it.

`open_conversation` doubles as the bootstrap: if the caller isn't in any conversation yet, it mints one (using `title` if supplied) and promotes it in a single call. No need to send a placeholder ask/notify first.

```
# Agent A (no prior conversation — bootstrapping the collab):
open_conversation(sender="Claude Win", title="Switchboard refactor collab")
# → "ok. open_conversation = <conv-id>"  (conv minted + promoted)

# Agent B (in their own separate conversation, told to join):
enter_conversation(sender="Claude WSL")
# → blocks; returns full conversation history when Agent A next speaks
```

## Combine

Two ongoing conversations merge into one. Call `combine_conversations(source_id, target_id)` from any agent (or John triggers it from the phone). Source ends; its members are moved to target. Dormant source members get auto-resumed via the launcher.

To find the conversation_id you need: `lookup_conversation_ids(title_contains="keyword")` or `lookup_conversation_ids(sender_contains="Claude Win")`.

```
# Find your partner's conversation:
lookup_conversation_ids(sender_contains="Claude WSL")
# → ["abc123"]

# Merge them in:
combine_conversations(source_id="abc123", target_id="<your-current-conv-id>")
# → "ok. combined <source_id> into <target_id> (N member(s))"
```

After being moved into a conversation via combine, call `enter_conversation(sender)` to queue yourself for an intro — peers will include your new context when they next speak.

## Spawn-into-existing

John spawns a new agent directly into an existing Active conversation via the phone's spawn dialog ("Add to existing" option). The new agent's prompt tells them which conversation they've joined and who's in it. Their first switchboard call should be `message_and_await_agent` with a brief intro, or `enter_conversation` if they want to receive context first.

## Away-mode auto-enable on spawn

When John spawns from the phone, global away mode is automatically enabled if it was off. A toast on the phone confirms the flip. Spawned agents start in away mode and should not call `set_away_mode(true)` themselves.

---

# Collaboration rules

These rules apply whenever you are in a multi-member conversation using `message_and_await_agent`:

1. Use `message_and_await_agent(sender="<you>", message="...")` to communicate with peers. Always pass your own sender.
2. Use `message_and_await_agent` only to speak to peers — not to John. For human communication use `ask_human` (away mode) or terminal output (at-desk), with `notify_human` for non-blocking status updates to John.
3. No meta-commentary. Respond with content directly.
4. **`message` is required and non-empty.** Calling with an empty or absent `message` returns `"ERROR: message is required. The 'listen without speaking' use case is enter_conversation()."`. The "listen without speaking" use case is served by `enter_conversation()`.
5. **Mid-collab symmetric obligation.** Receiving a message via `message_and_await_agent` passes the live baton to you. You MUST answer with another `message_and_await_agent` call carrying a non-empty `message`. Two failure modes are forbidden:
   - **No silent exit.** Ending your turn without replying leaves peers blocked indefinitely. Always pass the baton back or call `leave_conversation`.
   - **No deadlocking empty calls mid-session.** If your peer is blocked and you're about to call with no message, use `enter_conversation()` instead.
6. Critically review your partner's proposals. Be specific. Push back when you disagree, with concrete reasoning. Rubber-stamping is a failure mode.
7. Your goal is consensus on the task. When consensus is reached or debate becomes unproductive, `leave_conversation(sender, parting_message)` — include a clear parting summary. Exactly one agent reports the outcome to John.
8. If `message_and_await_agent` returns `"__CONVERSATION_EMPTY__"`, you are the sole alive member. Do NOT call `message_and_await_agent` again. Report to John (via `ask_human` if away mode, or terminal if at-desk) and end your turn.
9. If `message_and_await_agent` returns `"__TIMEOUT__"`, ping John for a status check (terminal if at-desk, `ask_human` if away-mode). Do not silently abandon peers.
10. If `message_and_await_agent` returns any other `"ERROR: ..."`, surface it to John immediately.
11. After making changes (code, files, configuration), verify them with appropriate tools (run tests, re-read the file, etc.) before claiming completion.

Title: optional on every Switchboard tool. **Set one on your first call.**

## Rate limit note

`notify_human` and `send_document_human` are rate-limited per conversation. If you hit the limit, you'll get `"ERROR: rate limit exceeded..."` with a wait time. Back off and retry after the indicated interval.

## Parallel openings on session start

By default, when multiple agents start together, each does its own initial research or analysis, then sends an opening position via `message_and_await_agent`. Each receives the partner's independent opening as its first delivery — treat that as the partner's opening position (not a reply to yours) and respond to it.

This intentional parallelism prevents one agent from anchoring on the other's framing, surfaces real disagreement at first contact, and roughly halves the wall-clock time before substantive exchange begins.

If John explicitly tells one agent to "listen first" or "wait for your partner," that agent begins by calling `enter_conversation(sender="<you>")` to queue in the wait queue without speaking. Use this only when explicitly directed — it's the exception, not the default.

## Leaving a conversation

When you believe consensus has been reached (or debate is deadlocked), call `leave_conversation(sender="<you>", parting_message="<final summary>")`. The parting message is appended to the conversation log and wakes any blocked peers.

**A verbal "I'm leaving" in a `message_and_await_agent` call is NOT a leave.** You must call the `leave_conversation` tool. Sending a message that says "I am leaving now" just leaves you blocked awaiting your peer's reply.

**Session-fallback after leaving**: your session is re-bound to your home conversation (away on) or unbound (away off). You are never orphaned.

**Reporting to John (the last agent in a conversation):**

Use `ask_human` as your single entry point. The server tells you which channel applies via its return value:

1. Call `ask_human(question=<consensus summary>, sender="<you>")`.
2. If it blocks and returns John's reply text, away-mode was active — you're done.
3. If it returns the literal string `"ERROR: John is at his desk. Ask this question via the terminal."`, away-mode is off. The summary has already been delivered to John's phone as a passive notification; now repeat it verbatim in the terminal — that's where John is watching.

## Away mode and conversations are independent

**Away mode** means John is not at the desk. ALL output must go through `notify_human`, `ask_human`, or `send_document_human` — no terminal text. Away mode is active when:
- You were spawned by Switchboard (phone auto-enables it).
- John explicitly says he is stepping away.

**Conversation (collab)** means you are in a multi-member conversation using `message_and_await_agent`. Conversation mode does NOT imply away mode.

When both modes are active: `message_and_await_agent` for peer communication, `ask_human`/`notify_human`/`send_document_human` for all human communication — no terminal output.

When conversation only (John has not stepped away): `message_and_await_agent` for peer communication; once consensus is reached or you are blocked, report back to John in the terminal normally.

---

# What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when John is at his desk and interacting with you via chat.
