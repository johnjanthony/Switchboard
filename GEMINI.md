# Switchboard — Gemini CLI Orientation

This file contains Gemini-specific instructions for working in the Switchboard workspace.

## Mandates

- **FOUNDATIONAL MANDATE:** You MUST read and adhere to the project-wide orientation and conventions in [`AGENTS.md`](AGENTS.md).
- **FOUNDATIONAL MANDATE:** You MUST read and adhere to the tool usage and away mode protocol in [`skill/SKILL.md`](skill/SKILL.md).

## Setup

To wire your Gemini CLI session to the local Switchboard gateway:

```bash
gemini mcp add switchboard http://localhost:9876/mcp --type http --trust
gemini skills link .\skill
```