# Switchboard

> A human-in-the-loop input gateway for Claude Code agents.

Switchboard is a locally-hosted MCP server that lets Claude Code agents pause mid-task and ask John a question via a native Android app or Telegram. Designed for away-from-desk workflows where John has stepped away but wants his agents to continue working unsupervised until they hit a decision that genuinely requires human input.

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

Switchboard reads its configuration from OS env vars. A `.env` file is loaded as a fallback if present — OS env wins.

### Core Configuration (Telegram)

Copy the template and fill in the values:

```bash
cp .env.example .env
# edit .env: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

Or set them as OS env vars:

```bash
export TELEGRAM_BOT_TOKEN="<token from @BotFather>"
export TELEGRAM_CHAT_ID="<your numeric chat id>"
```

**To find your chat ID:** message [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your numeric ID.

**Before your bot can message you:** open your bot in Telegram (search for its @username) and tap **Start** once. Telegram blocks bots from initiating conversations until the user opts in.

### Environment Variables

| Variable | Required | Default | Purpose |
| :--- | :--- | :--- | :--- |
| **Server Settings** | | | |
| `SWITCHBOARD_HOST` | No | `127.0.0.1` | Local bind address for the SSE/SSE server. |
| `SWITCHBOARD_PORT` | No | `9876` | Local port for the SSE/SSE server. |
| `SWITCHBOARD_TIMEOUT_SECONDS` | No | `86400` | How long `ask_human` blocks before returning `__TIMEOUT__`. |
| `SWITCHBOARD_LOG_PATH` | No | `./logs/switchboard.jsonl` | Path to the event audit log. |
| **Telegram Channel** | | | |
| `SWITCHBOARD_ENABLE_TELEGRAM` | No | `false` | Set to `true` to enable the Telegram bot backend. |
| `TELEGRAM_BOT_TOKEN` | If enabled | | Your bot token from [@BotFather](https://t.me/botfather). |
| `TELEGRAM_CHAT_ID` | If enabled | | Your numeric chat ID from [@userinfobot](https://t.me/userinfobot). |
| **Android & Firebase** | | | |
| `SWITCHBOARD_ENABLE_ANDROID` | No | `false` | Set to `true` to enable the Firebase backend and Android integration. |
| `FIREBASE_DATABASE_URL` | If enabled | | The URL of your Firebase Realtime Database (e.g. `https://proj.firebaseio.com/`). |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | If enabled | | Absolute path to your Firebase service account key JSON file. |
| `FIREBASE_STORAGE_BUCKET` | No | | Hostname of your Firebase Storage bucket (for document attachments). |
| **Session Spawning** | | | |
| `SWITCHBOARD_SPAWN_ROOT` | No | | Absolute path to the directory containing your project folders for `/spawn`. |

## Run

```bash
python -m server
```

The gateway binds to `127.0.0.1:9876` by default and exposes MCP over SSE at `/sse`.

## Wire Claude Code to it

Use the Claude Code CLI (recommended — registers at user scope):

```bash
claude mcp add switchboard --scope user --transport sse http://localhost:9876/sse
```

Or add it manually to your MCP config:

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

The skill teaches the agent when and how to use `ask_human` / `notify_human`:

```bash
mkdir -p ~/.claude/skills/switchboard
cp skill/SKILL.md ~/.claude/skills/switchboard/SKILL.md
```

## Android App

The project includes a native Android app in the `android/` directory.

### Build and Install
1.  **Credentials**: Download `google-services.json` from your Firebase project and place it in `android/app/`.
2.  **Open in Android Studio**: Open the root `Switchboard` folder.
3.  **Sync & Run**: Click the "Sync Project with Gradle Files" icon, then hit the **Run** button to install on your phone or emulator.

The app uses Firebase Cloud Messaging (FCM) for instant push notifications and Realtime Database for two-way communication with your agents.

## Using

### Away mode

Away mode activates when you tell your agent you're stepping away — any phrasing like *"I'm stepping away"* is sufficient. The agent immediately routes all output through `ask_human` or `notify_human` instead of the terminal.

- **`ask_human(question, agent_id)`** — blocks until John replies; returns the reply text, or `"__TIMEOUT__"` after 24 hours.
- **`notify_human(message, agent_id)`** — fire-and-forget status update; returns `"ok"` immediately.

To exit away mode, reply *"I'm back"* or equivalent. The agent switches back to normal terminal output.

### Replying to messages

Switchboard correlates your reply to the waiting `ask_human` call using the client's reply gesture. 

- **Telegram**: **Long-press the bot's message and tap Reply** before typing your answer. A standalone message (not a reply) will not be correlated and the agent will not unblock.
- **Android App**: Use the reply field at the bottom of the agent's chat tab.

### Spawning a new Claude Code session from Telegram

With `SWITCHBOARD_SPAWN_ROOT` configured, you can open a new Windows Terminal tab running a fresh Claude Code session directly from your phone:

```text
/spawn <project-key> <prompt>
```

`<project-key>` is a subdirectory name under `SWITCHBOARD_SPAWN_ROOT`. For example, if `SWITCHBOARD_SPAWN_ROOT=C:\Work` and you send `/spawn Switchboard review the test suite`, Switchboard opens a new tab running:

```powershell
claude -p "review the test suite" --dangerously-skip-permissions
```

in `C:\Work\Switchboard`.

**Prerequisites:**

- Set `SWITCHBOARD_SPAWN_ROOT` in `.env` and restart the service.
- Register the `SwitchboardSpawn` scheduled task (one-time, elevated PowerShell):

  ```powershell
  .\scripts\register-spawn-task.ps1
  ```

- The task fires in your interactive desktop session so Windows Terminal is reachable.

A 60-second rate limit prevents accidental double-spawns. The spawn is audit-logged to `logs/switchboard.jsonl`.

### Formatting Telegram messages

`ask_human` and `notify_human` accept an optional `format` parameter. The default is `"plain"`. Pass `format="html"` to send Telegram HTML-formatted messages — supported tags are `<b>`, `<i>`, `<code>`, `<pre>`, and `<a href="...">`. The agent_id prefix is auto-escaped; the message body is passed through as-is, so the agent is responsible for well-formed HTML.

## Manual smoke test

With the server running and an agent wired up:

1. In a Claude Code session, say: *"I'm stepping away — use ask_human for any decisions. Label yourself SmokeTest."*
2. Ask the agent to do something that should trigger a question, e.g. *"Delete the oldest file in `logs/`."*
3. Watch your phone: you should receive a Telegram message of the form `[SmokeTest | <request_id>] <question>`.
4. **Long-press the message and tap Reply**, then type your answer (e.g. "yes"). A plain new message will not work — correlation requires Telegram's reply-to gesture so the bot can see `reply_to_message.message_id`.
5. The agent's `ask_human` tool call unblocks with your reply text.
6. Check `logs/switchboard.jsonl` — you should see `request_created` and `request_resolved` events.

## Tests

```bash
pytest
```

All unit tests are offline; no Telegram creds required.

## Project layout

See [`CLAUDE.md`](CLAUDE.md) for the agent-oriented project tour, or design spec §11 for the canonical tree.
