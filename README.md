# Switchboard

> A human-in-the-loop input gateway for Claude Code agents.

Switchboard is a locally-hosted MCP server that allows one or more Claude Code agents running on the same workstation to pause mid-task and request human input — without hallucinating a decision or aborting the task. Responses can be provided from a companion terminal pane or from a mobile Telegram client, depending on whether the developer is at their desk or away.

## The Problem

Claude Code agents are capable of running long, complex, multi-step tasks autonomously. But some decisions — overwriting a file, running a migration, choosing between two approaches — genuinely require human judgment before proceeding. Without a mechanism for mid-task human input, agents either guess, abort, or require constant supervision.

Switchboard gives agents a reliable way to ask.

## How It Works

Each Claude Code agent connects to Switchboard via SSE as an MCP server. When an agent needs input, it calls the `ask_human` tool with a question. Switchboard:

1. Assigns a unique request ID and records the pending question
2. Sends a notification to the developer (terminal pane and/or Telegram)
3. Blocks the agent — deterministically — until a response arrives
4. Returns the response as the MCP tool result, and the agent continues

Multiple agents can have simultaneous pending requests. Each is tracked independently and resolved only when the correct response arrives.

## Features

- **Multi-agent support** — any number of Claude Code sessions connect to a single Switchboard instance
- **Dual response surfaces** — respond from a companion terminal pane (at desk) or Telegram (away)
- **Non-blocking notifications** — `notify_human` tool for status updates that don't require a response
- **Agent labeling** — each session identifies itself (e.g. "IR2", "DMX") so requests are clearly attributed
- **Configurable timeout** — agents receive a timeout signal if no response arrives within a set window

## Project Structure

```
switchboard/
├── README.md
├── docs/
│   └── design-spec.md          # Full architecture and option comparison
├── server/
│   └── ...                     # MCP gateway server (Python/asyncio)
├── companion/
│   └── ...                     # At-desk terminal companion script
└── CLAUDE.md                   # Instructions for Claude Code agents
```

## Status

Pre-implementation. Architecture decisions are being finalized. See [`docs/design-spec.md`](docs/design-spec.md) for full design details and option comparison.
