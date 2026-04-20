# Switchboard

> A human-in-the-loop input gateway for Claude Code agents.

Switchboard is a locally-hosted MCP server that lets Claude Code agents pause mid-task and ask the developer a question via Telegram. Designed for away-from-desk workflows where the developer has stepped away but wants their agents to continue working unsupervised until they hit a decision that genuinely requires human input.

See [`docs/superpowers/specs/2026-04-19-switchboard-design.md`](docs/superpowers/specs/2026-04-19-switchboard-design.md) for the full design.

## Install

```bash
git clone <this-repo>
cd switchboard
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
pip install -e ".[dev]"
```

## Configure

Set the required environment variables (OS env vars preferred; a `.env` file is loaded as a fallback if present):

```bash
export TELEGRAM_BOT_TOKEN="<token from @BotFather>"
export TELEGRAM_CHAT_ID="<your numeric chat id>"
```

To find your chat ID: message your bot any text, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The value at `result[-1].message.chat.id` is your chat ID.

Optional tuning:

```bash
export SWITCHBOARD_PORT=9876            # default 9876
export SWITCHBOARD_TIMEOUT_SECONDS=86400 # default 24 hours
export SWITCHBOARD_LOG_PATH=./logs/switchboard.jsonl
```

## Run

```bash
python -m server
```

The gateway binds to `127.0.0.1:9876` by default.

## Wire an agent to it

Add to the agent's MCP config (per-project or global):

```json
{
  "mcpServers": {
    "switchboard": {
      "type": "sse",
      "url": "http://localhost:9876/sse"
    }
  }
}
```

## Install the skill

Copy the skill file into your Claude Code skills directory:

```bash
mkdir -p ~/.claude/skills/switchboard
cp skill/SKILL.md ~/.claude/skills/switchboard/SKILL.md
```

## Manual smoke test

With the server running and an agent wired up:

1. In a Claude Code session, say: *"I'm stepping away — use ask_human for any decisions. Label yourself SmokeTest."*
2. Ask the agent to do something that should trigger a question, e.g. *"Delete the oldest file in logs/."*
3. Watch your phone: you should receive a Telegram message `[SmokeTest | xxxxxxxx] ...`.
4. Reply to that message with "yes" or similar.
5. The agent's `ask_human` tool call should unblock with your reply text.
6. Check `logs/switchboard.jsonl` — you should see `request_created` and `request_resolved` events.

## Tests

```bash
pytest
```

All unit tests are offline (no Telegram creds required).

## Project layout

See the design spec §11 for the canonical project layout.
