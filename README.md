# Switchboard

> A human-in-the-loop input gateway for Claude Code agents.

Switchboard is a locally-hosted MCP server that lets Claude Code agents pause mid-task and ask John a question via a native Android app. Designed for away-from-desk workflows where John has stepped away but wants his agents to continue working unsupervised until they hit a decision that genuinely requires human input.

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

### Environment Variables

| Variable | Required | Default | Purpose |
| :--- | :--- | :--- | :--- |
| **Server Settings** | | | |
| `SWITCHBOARD_HOST` | No | `127.0.0.1` | Local bind address for the SSE/SSE server. |
| `SWITCHBOARD_PORT` | No | `9876` | Local port for the SSE/SSE server. |
| `SWITCHBOARD_TIMEOUT_SECONDS` | No | `86400` | How long `ask_human` blocks before returning `__TIMEOUT__`. |
| `SWITCHBOARD_LOG_PATH` | No | `./logs/switchboard.jsonl` | Path to the event audit log. |
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
claude mcp add switchboard --scope user --transport http http://localhost:9876/mcp
```

Or add it manually to your MCP config:

```json
{
  "mcpServers": {
    "switchboard": {
      "type": "http",
      "url": "http://localhost:9876/mcp"
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

- **`ask_human(question, channel_id, sender?, format?, suggestions?)`** — blocks until John replies; returns the reply text, or `"__TIMEOUT__"` after 24 hours.
- **`notify_human(message, channel_id, sender?, format?)`** — fire-and-forget status update; returns `"ok"` immediately.
- **`send_document_human(path, channel_id, sender?, caption?)`** — delivers a file to John's phone; fire-and-forget.
- **`message_and_await_agent(channel_id, sender, message?)`** — collab sessions only; sends to partner agent and blocks until reply.

To exit away mode, reply *"I'm back"* or equivalent. The agent switches back to normal terminal output.

### Replying to messages

Switchboard correlates your reply to the waiting `ask_human` call via the Android app's reply input at the bottom of the channel tab. Type your answer and tap Send. If the question included suggestion buttons, tap one to reply instantly without typing.

### Spawning a new Claude Code session

With `SWITCHBOARD_SPAWN_ROOT` configured, you can open a new Windows Terminal tab running a fresh Claude Code session directly from the Android app. Tap the **spawn** button in the app, choose a project and enter a prompt, then tap **Spawn**. For collab sessions, enable the **Collab mode** checkbox — Switchboard opens two terminal tabs that communicate with each other through the gateway.

**Prerequisites:**

- Set `SWITCHBOARD_SPAWN_ROOT` in `.env` and restart the service.
- Register the `SwitchboardSpawn` scheduled task (one-time, elevated PowerShell):

  ```powershell
  .\scripts\register-spawn-task.ps1
  ```

- The task fires in your interactive desktop session so Windows Terminal is reachable.

A 60-second rate limit prevents accidental double-spawns. The spawn is audit-logged to `logs/switchboard.jsonl`.

### Formatting messages

`ask_human` and `notify_human` accept an optional `format` parameter. The default is `"plain"`. Pass `format="markdown"` to render the message with Markdown in the Android app — bold, italic, inline code, code blocks, and links are all supported. Use standard Markdown syntax.

## Manual smoke test

With the server running, the Android app installed, and an agent wired up:

1. Open a Claude Code session and spawn an agent via the Android app (or say *"I'm stepping away"* to an existing session).
2. Ask the agent to do something that should trigger a question, e.g. *"Delete the oldest file in `logs/`."*
3. Watch your phone: you should receive a push notification and see the question appear in the channel tab.
4. Type your answer in the reply field at the bottom and tap Send (or tap a suggestion button if provided).
5. The agent's `ask_human` tool call unblocks with your reply text.
6. Check `logs/switchboard.jsonl` — you should see `request_created` and `request_resolved` events.

## Tests

```bash
pytest
```

All unit tests are offline; no credentials required.

## Project layout

See [`CLAUDE.md`](CLAUDE.md) for the agent-oriented project tour, or design spec §11 for the canonical tree.
