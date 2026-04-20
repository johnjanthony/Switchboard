---
name: switchboard
description: Use the ask_human and notify_human MCP tools to interact with the developer via Telegram while they are away from their desk. Invoke ask_human whenever a decision would otherwise stall the task (file overwrite, migration, ambiguous intent, permission to proceed). Invoke notify_human for non-blocking status updates.
---

# Switchboard

Switchboard is a local MCP gateway that lets you reach the developer on their phone while they are away from the desk. It exposes two tools:

- **`ask_human(question, agent_id)`** — blocks until the developer replies. Returns the reply text, or the sentinel string `"__TIMEOUT__"` if no reply arrives within the server's timeout window (default 24h).
- **`notify_human(message, agent_id)`** — fire-and-forget status update. Returns `"ok"` immediately.

## When to use it

The developer activates away mode by telling you something like:

> "I'm stepping away. Use the ask_human MCP tool for any questions or decisions that would normally require my input. I'll respond via Telegram."

Once in away mode, you must route **every** question that would otherwise go to the VS Code chat through `ask_human` instead. Do not guess at decisions that need human judgment. Do not abort. Do not wait silently.

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

## What not to use it for

- Do not call `ask_human` for decisions you can make yourself with the information in front of you. Away mode is not permission to defer judgment calls that do not require human input.
- Do not call `ask_human` for purely informational status ("I'm about to run the tests") — that is `notify_human`.
- Do not call either tool when the developer is at their desk and interacting with you via chat.
