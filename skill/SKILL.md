---
name: switchboard
description: Use the ask_human and notify_human MCP tools to interact with the developer via Telegram while they are away from their desk. Invoke ask_human whenever a decision would otherwise stall the task (file overwrite, migration, ambiguous intent, permission to proceed). Invoke notify_human for non-blocking status updates.
---

# Switchboard

Switchboard is a local MCP gateway that lets you reach the developer on their phone while they are away from the desk. It exposes three tools:

- **`ask_human(question, agent_id, format?, suggestions?)`** — blocks until the developer replies. Returns the reply text, or the sentinel string `"__TIMEOUT__"` if no reply arrives within the server's timeout window (default 24h).
- **`notify_human(message, agent_id, format?)`** — fire-and-forget status update. Returns `"ok"` immediately.
- **`send_document_human(path, agent_id, caption?)`** — deliver a file to the developer on Telegram. Fire-and-forget. Returns `"ok"` or `"ERROR: ..."`. See constraints below.

## When to use it

Away mode activates whenever the developer says they are stepping away — any phrasing like "I'm stepping away", "stepping away", or "going away mode" is sufficient. No explicit "use ask_human" instruction is required.

**When away mode activates, do not produce any text response in the terminal.** Make a tool call immediately:

- If idle or between tasks: `ask_human` to confirm you have entered away mode and ask what's next.
- If mid-task: `notify_human` to report current status, followed by `ask_human` to confirm next steps.

Your trained default is to produce a text response. In away mode, intercept that instinct before it fires. If you notice yourself composing a reply in the terminal — stop. Make the tool call instead. The tool call is the acknowledgment; any text in the terminal is a failure.

**Receiving a reply to `ask_human` does not exit away mode.** The same instinct will fire again after a Telegram reply lands — the urge to confirm receipt in the terminal ("got it", "test confirmed", "buttons working", etc.) is also a failure. When `ask_human` returns, treat the return value as input for your next tool call, not as a trigger to type a chat response. Your next output after any reply — including tap-button responses — must be via `ask_human` or `notify_human`, never in the terminal.

Once in away mode, route **every** output through `ask_human` or `notify_human`. Do not guess at decisions that need human judgment. Do not abort. Do not wait silently in chat.

**The only exit from away mode is the developer explicitly saying they are back at their desk** ("I'm back", "back at my desk", etc.). When that message arrives — whether as a reply to `ask_human` or in chat — immediately switch back to normal terminal output. Do not issue another `ask_human` to acknowledge it.

At desk (not in away mode), interact with the developer normally through chat — Switchboard is not needed.

## Choosing an `agent_id`

The `agent_id` is a short human-meaningful label that appears in every Telegram message so the developer knows which agent is asking. In order of preference:

1. **Use a label the developer gave you.** If they said "call yourself IR2" or "label these as migration-work", use that label for every call during the session.
2. **Otherwise derive one from the current task.** A short 1-3 word label based on what you are working on: `DMXRefactor`, `IR2Migration`, `DocGen`. Pick it the first time you call `ask_human`, then reuse it for every subsequent call in the same session.

Keep the label stable across calls within a session. The developer should be able to tell at a glance that two messages are from the same agent.

## Response conventions

- Be concise in questions. The developer is on their phone. One or two sentences.
- Include enough context that the developer can decide without opening their laptop. Include file paths, commit IDs, or the specific ambiguity you need resolved.
- Suggest a default when there is one: "Overwrite foo.java with the refactored version? (default: yes)".
- For multi-choice, put the options in the question: "Use ActiveMQ or Kafka for the new event bus?"

## Suggestion buttons

`ask_human` accepts an optional `suggestions` list. When provided, Telegram renders tap-able inline buttons below the question — the developer taps a button and its label is returned as the response string.

```
ask_human("Overwrite foo.java?", agent_id, suggestions=["yes", "no", "abort"])
# → returns "yes", "no", or "abort", or a typed free-text reply
```

Use suggestions for binary or small-choice decisions where tapping beats typing on mobile. Keep suggestion labels short (under 64 characters each). When suggestions are present, the reply-to gesture is not forced — but the developer can still type a free-text reply using Telegram's manual reply gesture if they want to say something other than the suggestions.

## Formatting messages

Both tools accept an optional `format` parameter: `"plain"` (default) or `"html"`.

When `format="html"`, Telegram renders the message with rich formatting. Use HTML tags — **not** Markdown syntax. Supported tags: `<b>bold</b>`, `<i>italic</i>`, `<code>inline code</code>`, `<pre>code block</pre>`, `<a href="url">link</a>`.

You are responsible for well-formed HTML when using `format="html"`. The gateway escapes the agent_id and request_id prefix automatically, but the message body is passed through as-is — malformed tags will cause Telegram to reject the message.

Use `format="html"` when the message contains structure that benefits from formatting: numbered lists with bold headers, code snippets, file paths. Keep plain-text messages as `format="plain"` (the default) — don't wrap simple one-liners in HTML.

**Never use Markdown syntax** (`**bold**`, `_italic_`, backtick code fences) — Switchboard does not support Markdown or MarkdownV2. Markdown characters sent in plain mode appear as literal characters in Telegram.

## Handling `"__TIMEOUT__"`

If `ask_human` returns `"__TIMEOUT__"`, the developer did not reply within the window. Do not guess and continue. Instead:

1. Record what you were about to do and why you needed input.
2. Pause the current work stream. Do not take irreversible actions.
3. When the developer returns, resume from where you paused.

Use `notify_human` to record the pause if it is helpful context for later: `notify_human("Paused DMXRefactor — timed out waiting on approval to overwrite CustomerMapper.java", "DMXRefactor")`.

## Handling `"ERROR: ..."`

If `ask_human` returns a string starting with `"ERROR:"`, the gateway itself failed (e.g., Telegram unreachable). Treat this the same as a timeout — pause, do not guess.

## Staying alive in away mode

While in away mode, after completing a discrete task **that the developer handed to you** (not merely an intermediate step within that task — not running tests, not reading files, not committing), call:

```
ask_human("Task done: <one-line summary>. What's next?", agent_id)
```

instead of ending your turn. This keeps the session alive so the developer can queue additional work from their phone without needing to re-spawn you.

Treat `"__TIMEOUT__"` as permission to end the session gracefully.

The "discrete task the developer handed to you" phrasing is load-bearing — do not ping between internal subtasks.

## Sending files with `send_document_human`

Use this to deliver generated reports, diffs, logs, or spec documents to the developer's phone for review. It is fire-and-forget — the agent does not block waiting for a reply. Per the "never end on fire-and-forget" rule, there must always be at least one `ask_human` after any `send_document_human` call.

**Constraints enforced by the gateway (violations return `"ERROR: ..."`):**

- `path` may be **absolute** or **relative**. Relative paths are resolved against the project's current working directory; `..` traversal that escapes the project root is rejected. Absolute paths are accepted as-is.
- Maximum file size: **5 MB**.
- Denied filenames: `.env`, `service-account.json` (exact match), and anything matching `*token*`, `*secret*`, `*.pem`, `*.key`, `.env*`, `*.env` (case-insensitive glob — covers `.env.local`, `.envrc`, `prod.env`, etc.).
- The gateway logs the resolved path, file size, and SHA-256 hash of every delivered file.

**`caption`** is optional (max 1024 characters). Use it to give the developer context: `"Migration diff — 47 tables affected"`.

**Example:**
```
send_document_human("logs/migration-diff.txt", "DBMigrate", "Schema diff for review")
```

## What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when the developer is at their desk and interacting with you via chat.
