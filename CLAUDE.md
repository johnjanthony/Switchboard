# Switchboard — Claude Code Orientation

This file contains Claude-specific instructions for working in the Switchboard workspace.

## Mandates

- **Read [`AGENTS.md`](AGENTS.md)** for project shape, layout, conventions, and service management.
- **Read [`skill/SKILL.md`](skill/SKILL.md)** for MCP tool signatures and the Away Mode protocol.

## Setup

To wire your Claude Code session to the local Switchboard gateway:

```bash
claude mcp add switchboard --scope user --transport http http://localhost:9876/mcp
# Copy skill/SKILL.md to ~/.claude/skills/switchboard/SKILL.md
```