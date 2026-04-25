---
name: switchboard
description: Use the ask_human and notify_human MCP tools to interact with John while he's away from his desk. Invoke ask_human whenever a decision would otherwise stall the task (file overwrite, migration, ambiguous intent, permission to proceed). Invoke notify_human for non-blocking status updates.
---

# CRITICAL: Away Mode Protocol

**If John has stepped away, YOU MUST NOT PRODUCE ANY TEXT OUTPUT IN THE TERMINAL.**

This is the single most important rule. If you produce chat text in the terminal after John has left, you have failed the protocol. Intercept your instinct to acknowledge tasks ("Okay", "On it", "Done") or report status in the terminal.

**The tool call IS the acknowledgment.**

**Mark the session:** When entering away mode, call `enter_away_mode()` alongside the confirming `notify_human` call. When exiting, call `exit_away_mode()` as the first action before resuming terminal output. Spawned sessions auto-enter on spawn — do not call `enter_away_mode()` redundantly at spawn time.

**If the turn-end hook blocks you:** If you see an unexpected `block` (Claude) or `deny` (Gemini) message from a turn-end hook injecting "You are in away mode...", the flag is still active. Either call `ask_human()` to check in with John, or — only if John explicitly told you he is back — call `exit_away_mode()` first and then resume terminal output.

1.  **Entry:** When John says "stepping away", "going away mode", "I'm away", or any equivalent phrasing — even when bundled with other directives in the same message — the trigger fires.
    -   **`enter_away_mode()` MUST be your first tool call**, before any other work the message asks you to do. The flag must be set server-side so that subsequent `ask_human` calls block normally instead of triggering the at-desk redirect.
    -   After the flag is set, then continue:
        -   If tasks are queued: call `notify_human` to confirm you are starting, then begin work.
        -   If idle: call `ask_human` to ask what's next.
        -   If mid-task: call `notify_human` to report status, then `ask_human` for next steps.
    -   For spawned sessions, the flag is already set at spawn time — skip the `enter_away_mode()` call.
    -   **Zero terminal text.** A compound user message like "I'm stepping away. Call send_document_human with foo.txt, then ask_human about delivery." is NOT a license to skip `enter_away_mode()` and dive into the explicit commands. Set the flag first, then do the work.
2.  **Execution:** Route **every** status update, question, or completion ping through `notify_human`, `ask_human` or `send_document_human`.
3.  **Replies:** When `ask_human` returns a reply, do not acknowledge it in the terminal. Treat it as input for your next tool call or task.
4.  **Exit:** The **only** exception is when John explicitly says he is back ("I'm back", "back at desk"). Call `exit_away_mode()` as your first action, then resume terminal chat with a concise summary of your work.

**At-desk `ask_human` redirect.** If `ask_human` returns the literal string `"ERROR: John is at his desk. Ask this question via the terminal."`, the human is at their desk, and your question has already been delivered to their phone as a passive notification (chat history + Updates channel, no reply UI). Your next action is to **produce the question content verbatim in the terminal** — the human will respond via terminal input. Do NOT retry `ask_human`, do NOT call `exit_away_mode` (the flag is already off), and do NOT treat this as an error worth surfacing beyond asking the question. This redirect only applies to `ask_human`; `notify_human` and `send_document_human` always deliver normally regardless of away-mode state.

---

# Switchboard MCP Tools

Switchboard is a local MCP gateway that lets you reach John on his phone while he's away from his desk. It exposes six tools:

- **`ask_human(question, channel_id, sender?, format?, suggestions?)`** — blocks until John replies. Returns reply text or `"__TIMEOUT__"`.
- **`notify_human(message, channel_id, sender?, format?)`** — fire-and-forget. Returns `"ok"`.
- **`send_document_human(path, channel_id, sender?, caption?)`** — deliver a file. Fire-and-forget. Returns `"ok"` or `"ERROR: ..."`.
- **`message_and_await_agent(channel_id, sender, message?)`** — collab sessions only. Send to partner and block.
- **`enter_away_mode()`** — mark this Switchboard session as away mode. The turn-end hook will block agent turns that end in terminal output. Idempotent. Call when John says he is stepping away (spawned sessions auto-enter — no call needed at spawn time).
- **`exit_away_mode()`** — mark this Switchboard session as at-desk. The turn-end hook stops blocking. Idempotent. Call as the first action when John explicitly returns to the desk.

## Choosing a `channel_id`

`channel_id` is the routing key that pins all messages for a session to the same Android tab. Use it for **every** tool call — `ask_human`, `notify_human`, `send_document_human`, and `message_and_await_agent` — and do not vary it mid-session.

Three cases for where it comes from:

1. **Spawned sessions** — use the `channel_id` from your spawn prompt verbatim. Do not derive or vary.
2. **BYO collab (two agents sharing a channel)** — John provides the `channel_id` so both agents agree on it. Do not invent one.
3. **BYO single-agent (you are solo and none was provided)** — generate one yourself on first use and stick with it for the rest of the session. Format: `{sender-lowercased}-{YYYYMMDD}-{HHMMSS}` in UTC (e.g. `gemini-20260423-224530`, `claude-20260423-225415`).

Never ask John to pick a channel_id in case 3 — it's the agent's job to produce one. Do ask in case 2, only if you're somehow in a collab situation without a shared value.

## Choosing a `sender`

`sender` is your display name in the conversation. It appears in the chat bubble on John's phone. Use your active agent name (e.g., `"Claude"`, `"Gemini"`, `"Cloude"`, or `"Sparkles"`), or the one provided in your spawn prompt as the sender.
## Response conventions

- Be concise in questions. John is on his phone. One or two sentences.
- Include enough context that John can decide without opening his laptop. Include file paths, commit IDs, or the specific ambiguity you need resolved.
- Suggest a default when there is one: "Overwrite foo.java with the refactored version? (default: yes)".
- For multi-choice, put the options in the question: "Use ActiveMQ or Kafka for the new event bus?"

## Suggestion buttons

`ask_human` accepts an optional `suggestions` list. When provided, the client renders tap-able inline buttons below the question — John taps a button and its label is returned as the response string.

```
ask_human("Overwrite foo.java?", channel_id="switchboard-20260422-143052", suggestions=["yes", "no", "abort"])
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
  channel_id="switchboard-20260422-143052",
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

Use `notify_human` to record the pause if it is helpful context for later: `notify_human("Paused DMXRefactor — timed out waiting on approval to overwrite CustomerMapper.java", channel_id="switchboard-20260422-143052")`.

## Handling `"ERROR: ..."`

If `ask_human` returns a string starting with `"ERROR:"`, the gateway itself failed. Treat this the same as a timeout — pause, do not guess. If possible, use a shell command to check if the Switchboard server process is still running (e.g., `netstat -ano | findstr :9876`) to diagnose the failure before pausing.

**Exception:** the literal string `"ERROR: John is at his desk. Ask this question via the terminal."` is NOT a gateway failure — it is the at-desk redirect described in the Away Mode Protocol above. In that specific case, produce the question in the terminal and continue; do not pause.

## Staying alive in away mode

While in away mode, after completing a discrete task **that John handed to you** (not merely an intermediate step within that task — not running tests, not reading files, not committing), call:

```
ask_human("Task done: <one-line summary>. What's next?", channel_id="switchboard-20260422-143052")
```

instead of ending your turn. This keeps the session alive so John can queue additional work from his phone without needing to re-spawn you.

Treat `"__TIMEOUT__"` as permission to end the session gracefully.

The "discrete task John handed to you" phrasing is load-bearing — do not ping between internal subtasks.

## Sending files with `send_document_human`

Use this to deliver generated reports, diffs, logs, or spec documents to John's phone for review. It is fire-and-forget — the agent does not block waiting for a reply. Per the "never end on fire-and-forget" rule, there must always be at least one `ask_human` after any `send_document_human` call.

**Constraints enforced by the gateway (violations return `"ERROR: ..."`):**

- `path` may be **absolute** or **relative**. Relative paths are resolved against the project's current working directory; `..` traversal that escapes the project root is rejected. Absolute paths are accepted as-is.
- Maximum file size: **5 MB**.
- Denied filenames: `.env`, `service-account.json` (exact match), and anything matching `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env` (case-insensitive glob — covers `.env.local`, `.envrc`, `prod.env`, etc.).
- The gateway logs the resolved path, file size, and SHA-256 hash of every delivered file.

**`caption`** is optional (max 1024 characters). Use it to give John context: `"Migration diff — 47 tables affected"`.

**Example:**
```
send_document_human("logs/migration-diff.txt", channel_id="switchboard-20260422-143052", caption="Schema diff for review")
```

## Collab sessions

### `message_and_await_agent(channel_id, sender, message?)`

Sends `message` to your partner (if provided), then blocks until your partner
replies or a human injects a message.

- **`channel_id`** — from your spawn prompt, or provided by John for BYO sessions
- **`sender`** — your own sender name, unique within the session
- **`message`** — optional outbound text; omit if John has told you to wait for your partner

**If `message_and_await_agent` returns `"__TIMEOUT__"`:** if in away mode, call
`ask_human` to check in with John. If not in away mode, report in the terminal.
**If `message_and_await_agent` returns an error string (starts with `"ERROR:"`):**
handle the same way as a timeout.

### Away mode and collab mode are independent

**Away mode** means John is not at the desk. ALL output must go through
`notify_human`, `ask_human`, or `send_document_human` — no terminal text. Away
mode is active when:
- You were spawned by Switchboard (single-agent or collab)
- John explicitly says he is stepping away

**Collab mode** means you are paired with a second agent via
`message_and_await_agent`. Collab mode does NOT imply away mode.

When both modes are active: `message_and_await_agent` for peer communication,
`ask_human`/`notify_human`/`send_document_human` for all human communication —
no terminal output. If John steps away during an active BYO collab session,
continue using the same `channel_id` for everything — the session is already
registered and the Android tab already exists. The only change is that human
communication moves from the terminal to `ask_human` / `notify_human` with that
same `channel_id`.

When collab mode only (BYO, John has not stepped away): `message_and_await_agent`
for peer communication; once consensus is reached or you are blocked, report back
to John in the terminal normally.

### Collab protocol

1. If John told you to wait for your partner, call `message_and_await_agent(
   channel_id=..., sender=...)` with no message. Block until your partner speaks.
2. If John gave you work to do first, complete it then call
   `message_and_await_agent(channel_id=..., sender=..., message="...")`.
3. Exchange continues — each agent replies by calling `message_and_await_agent`
   with their response as `message`. If both agents began with a message, the
   first response each receives will be their partner's independent opening
   position rather than a reply to theirs — this is normal, treat it as their
   opening position and respond to it.
4. When consensus is reached:
   - **Away mode active:** call `ask_human` to confirm with John.
   - **Away mode not active:** respond in the terminal.
5. If debate becomes unproductive:
   - **Away mode active:** call `ask_human` to report the deadlock.
   - **Away mode not active:** report in the terminal.

### Bring your own session

John can initiate a collab session between two already-running agents by providing
both with a shared `channel_id`. Each agent uses their own display name as `sender`
(e.g. `"Claude"`, `"Gemini"`) — this is naturally unique across different agent types.
If John needs a specific name (e.g. two Claude instances), he will tell you.

Call `message_and_await_agent` as described in the collab protocol above. No
special setup is required — the first agent to call creates the session. Call
ordering does not matter; the gateway handles timing transparently. A third
distinct sender gets `"ERROR: session is full"`.

BYO sessions do not imply away mode. Unless John has also said he is stepping
away, report back to him in the terminal once the collab exchange concludes.

## What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when John is at his desk and interacting with you via chat.
