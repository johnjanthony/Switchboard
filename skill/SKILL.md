---
name: switchboard
description: Use the ask_human and notify_human MCP tools to interact with the user while he's away from his desk. Invoke ask_human whenever a decision would otherwise stall the task (file overwrite, migration, ambiguous intent, permission to proceed). Invoke notify_human for non-blocking status updates.
---

# Away mode — read this first

Away mode activates whenever the user says he is stepping away — any phrasing like "I'm stepping away", "stepping away", or "going away mode" is sufficient. No explicit "use ask_human" instruction is required.

**When away mode activates, do not produce any text response in the terminal.** Make a tool call immediately:

- If the user gave you tasks before stepping away: `notify_human` to confirm you have entered away mode and are starting work, then proceed. No `ask_human` needed for entry — you don't need a response.
- If idle (no queued tasks): `ask_human` to confirm you have entered away mode and ask what's next.
- If mid-task: `notify_human` to report current status, followed by `ask_human` to confirm next steps.

Your trained default is to produce a text response. In away mode, intercept that instinct before it fires. If you notice yourself composing a reply in the terminal — stop. Make the tool call instead. The tool call is the acknowledgment; any text in the terminal is a failure.

**Receiving a reply to `ask_human` does not exit away mode.** The same instinct will fire again after a reply lands — the urge to confirm receipt in the terminal ("got it", "test confirmed", "buttons working", etc.) is also a failure. When `ask_human` returns, treat the return value as input for your next tool call, not as a trigger to type a chat response. Your next output after any reply — including tap-button responses — must be via `ask_human` or `notify_human`, never in the terminal.

Once in away mode, route **every** output through `ask_human` or `notify_human`. Do not guess at decisions that need human judgment. Do not abort. Do not wait silently in chat.

**The only exit from away mode is the user explicitly saying he is back at his desk** ("I'm back", "back at my desk", etc.). When that message arrives — whether as a reply to `ask_human` or in chat — immediately switch back to normal terminal output. Do not issue another `ask_human` to acknowledge it.

When you resume terminal interaction, provide a concise summary of what was accomplished while away:
"Welcome back! While you were away, I completed [X] and [Y]. I am currently [status/next step]."

At desk (not in away mode), interact with the user normally through chat — Switchboard is not needed.

# Switchboard

Switchboard is a local MCP gateway that lets you reach the user on his phone while he's away from his desk. It exposes four tools:

- **`ask_human(question, channel_id, sender?, format?, suggestions?)`** — blocks until the user replies. Returns reply text or `"__TIMEOUT__"`.
- **`notify_human(message, channel_id, sender?, format?)`** — fire-and-forget. Returns `"ok"`.
- **`send_document_human(path, channel_id, sender?, caption?)`** — deliver a file. Fire-and-forget. Returns `"ok"` or `"ERROR: ..."`.
- **`message_and_await_agent(channel_id, sender, message?)`** — collab sessions only. Send to partner and block.

## Choosing a `channel_id`

`channel_id` is provided in your spawn prompt. Use it for **every** tool call — `ask_human`, `notify_human`, `send_document_human`, and `message_and_await_agent`. Do not derive or vary it.

## Choosing a `sender`

`sender` is your display name in the conversation. It appears in the chat bubble on the user's phone. Use your active agent name (e.g., `"Gemini"`, `"Sparkles"`, or `"Claude"`) as the sender. In collab sessions, use the sender name (`"Agent 1"` or `"Agent 2"`) provided in your spawn prompt.

## Response conventions

- Be concise in questions. the user is on his phone. One or two sentences.
- Include enough context that the user can decide without opening his laptop. Include file paths, commit IDs, or the specific ambiguity you need resolved.
- Suggest a default when there is one: "Overwrite foo.java with the refactored version? (default: yes)".
- For multi-choice, put the options in the question: "Use ActiveMQ or Kafka for the new event bus?"

## Suggestion buttons

`ask_human` accepts an optional `suggestions` list. When provided, the client renders tap-able inline buttons below the question — the user taps a button and its label is returned as the response string.

```
ask_human("Overwrite foo.java?", channel_id="switchboard-20260422-143052", suggestions=["yes", "no", "abort"])
# → returns "yes", "no", or "abort", or a typed free-text reply
```

Use suggestions for binary or small-choice decisions where tapping beats typing on mobile. Keep suggestion labels short (under 64 characters each). When suggestions are present, the user can still type a free-text reply if they want to say something other than the suggestions.

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

If `ask_human` returns `"__TIMEOUT__"`, the user did not reply within the window. Do not guess and continue. Instead:

1. Record what you were about to do and why you needed input.
2. Pause the current work stream. Do not take irreversible actions.
3. When the user returns, resume from where you paused.

Use `notify_human` to record the pause if it is helpful context for later: `notify_human("Paused DMXRefactor — timed out waiting on approval to overwrite CustomerMapper.java", channel_id="switchboard-20260422-143052")`.

## Handling `"ERROR: ..."`

If `ask_human` returns a string starting with `"ERROR:"`, the gateway itself failed. Treat this the same as a timeout — pause, do not guess. If possible, use a shell command to check if the Switchboard server process is still running (e.g., `netstat -ano | findstr :9876`) to diagnose the failure before pausing.

## Staying alive in away mode

While in away mode, after completing a discrete task **that the user handed to you** (not merely an intermediate step within that task — not running tests, not reading files, not committing), call:

```
ask_human("Task done: <one-line summary>. What's next?", channel_id="switchboard-20260422-143052")
```

instead of ending your turn. This keeps the session alive so the user can queue additional work from his phone without needing to re-spawn you.

Treat `"__TIMEOUT__"` as permission to end the session gracefully.

The "discrete task the user handed to you" phrasing is load-bearing — do not ping between internal subtasks.

## Sending files with `send_document_human`

Use this to deliver generated reports, diffs, logs, or spec documents to the user's phone for review. It is fire-and-forget — the agent does not block waiting for a reply. Per the "never end on fire-and-forget" rule, there must always be at least one `ask_human` after any `send_document_human` call.

**Constraints enforced by the gateway (violations return `"ERROR: ..."`):**

- `path` may be **absolute** or **relative**. Relative paths are resolved against the project's current working directory; `..` traversal that escapes the project root is rejected. Absolute paths are accepted as-is.
- Maximum file size: **5 MB**.
- Denied filenames: `.env`, `service-account.json` (exact match), and anything matching `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env` (case-insensitive glob — covers `.env.local`, `.envrc`, `prod.env`, etc.).
- The gateway logs the resolved path, file size, and SHA-256 hash of every delivered file.

**`caption`** is optional (max 1024 characters). Use it to give the user context: `"Migration diff — 47 tables affected"`.

**Example:**
```
send_document_human("logs/migration-diff.txt", channel_id="switchboard-20260422-143052", caption="Schema diff for review")
```

## Collab sessions

### `message_and_await_agent(channel_id, sender, message?)`

Sends `message` to your partner (if provided), then blocks until your partner replies or a human injects a message.

- **`channel_id`** — from your spawn prompt (e.g. `myproject-20260422-143052`)
- **`sender`** — your own sender from your spawn prompt (`"Agent 1"` or `"Agent 2"`)
- **`message`** — optional outbound text; omit on your first call if you are Agent 2

**If `message_and_await_agent` returns `"__TIMEOUT__"`:** call `ask_human` to check in with the user. Do not silently exit.
**If `message_and_await_agent` returns an error string (starts with `"ERROR:"`):** call `ask_human` immediately.

### Collab protocol

1. Agent 1: work on the task, then call `message_and_await_agent(channel_id=..., sender="Agent 1", message=...)`.
2. Agent 2: call `message_and_await_agent(channel_id=..., sender="Agent 2")` with no message on startup to listen.
3. When consensus is reached: call `ask_human(question, channel_id=..., sender="Agent 1")` to confirm with the user.
4. If debate is unproductive: call `ask_human` to report the deadlock.
5. Use `ask_human` and `notify_human` for all human communication with the same `channel_id` and `sender` — same rules as standard away mode apply.

## What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when the user is at his desk and interacting with you via chat.
