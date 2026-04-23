---
name: switchboard
description: Use the ask_human and notify_human MCP tools to interact with John while he's away from his desk. Invoke ask_human whenever a decision would otherwise stall the task (file overwrite, migration, ambiguous intent, permission to proceed). Invoke notify_human for non-blocking status updates.
---

# CRITICAL: Away Mode Protocol

**If John has stepped away, YOU MUST NOT PRODUCE ANY TEXT OUTPUT IN THE TERMINAL.**

This is the single most important rule. If you produce chat text in the terminal after John has left, you have failed the protocol. Intercept your instinct to acknowledge tasks ("Okay", "On it", "Done") or report status in the terminal.

**The tool call IS the acknowledgment.**

1.  **Entry:** When John says "stepping away" or similar:
    -   If tasks are queued: call `notify_human` to confirm you are starting, then begin work.
    -   If idle: call `ask_human` to ask what's next.
    -   **Zero terminal text.**
2.  **Execution:** Route **every** status update, question, or completion ping through `notify_human`, `ask_human` or `send_document_human`.
3.  **Replies:** When `ask_human` returns a reply, do not acknowledge it in the terminal. Treat it as input for your next tool call or task.
4.  **Exit:** The **only** exception is when John explicitly says he is back ("I'm back", "back at desk"). Only then do you resume terminal chat with a concise summary of your work. 

---

# Switchboard MCP Tools

Switchboard is a local MCP gateway that lets you reach John on his phone while he's away from his desk. It exposes four tools:

- **`ask_human(question, channel_id, sender?, format?, suggestions?)`** — blocks until John replies. Returns reply text or `"__TIMEOUT__"`.
- **`notify_human(message, channel_id, sender?, format?)`** — fire-and-forget. Returns `"ok"`.
- **`send_document_human(path, channel_id, sender?, caption?)`** — deliver a file. Fire-and-forget. Returns `"ok"` or `"ERROR: ..."`.
- **`message_and_await_agent(channel_id, sender, message?)`** — collab sessions only. Send to partner and block.

## Choosing a `channel_id`

`channel_id` is provided in your spawn prompt. Use it for **every** tool call — `ask_human`, `notify_human`, `send_document_human`, and `message_and_await_agent`. Do not derive or vary it.

## Choosing a `sender`

`sender` is your display name in the conversation. It appears in the chat bubble on John's phone. Use your active agent name (e.g., `"Claude"`, `"Gemini"`, `"Cloude"`, or `"Sparkles"`), or then one provided in your spawn prompt as the sender.
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

Sends `message` to your partner (if provided), then blocks until your partner replies or a human injects a message.

- **`channel_id`** — from your spawn prompt (e.g. `myproject-20260422-143052`)
- **`sender`** — your own sender from your spawn prompt
- **`message`** — optional outbound text; omit on your first call if you are the second agent to start

**If `message_and_await_agent` returns `"__TIMEOUT__"`:** call `ask_human` to check in with John. Do not silently exit.
**If `message_and_await_agent` returns an error string (starts with `"ERROR:"`):** call `ask_human` immediately.

### Collab protocol

1. First agent: work on the task, then call `message_and_await_agent(channel_id=..., sender=..., message=...)`.
2. Second agent: call `message_and_await_agent(channel_id=..., sender=...)` with no message on startup to listen.
3. When consensus is reached: call `ask_human(question, channel_id=..., sender=...)` to confirm with John.
4. If debate is unproductive: call `ask_human` to report the deadlock.
5. Use `ask_human` and `notify_human` for all human communication with the same `channel_id` and `sender` — same rules as standard away mode apply.

## What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when John is at his desk and interacting with you via chat.
