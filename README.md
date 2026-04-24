# Switchboard

> A human-in-the-loop input gateway for AI agents (Claude, Gemini, etc.).

Switchboard is a locally-hosted MCP server that lets AI agents pause mid-task and ask John a question via a native Android app. Designed for away-from-desk workflows where you want your agents to continue working unsupervised until they hit a decision that genuinely requires human input.

## Features

- **Unified Routing**: Target specific sessions using stable `channel_id`s and display names (`sender`).
- **Asynchronous Updates**: Send non-blocking notifications or deliver documents directly to your phone.
- **In-line Replies**: View your responses directly in the chat history for full context.
- **Activity Indicators**: Prominent high-visibility tab borders and tints for unseen activity or pending questions.
- **Session Spawning**: Launch fresh agent sessions on your desktop directly from your phone.
- **Bring-Your-Own Sessions**: Pair two already-running agents into a collab channel without spawning — just share a `channel_id`.
- **Rich Markdown**: Full support for bold, italic, code blocks, checklists, and tables.

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
| `SWITCHBOARD_HOST` | No | `127.0.0.1` | Local bind address for the SSE/HTTP server. |
| `SWITCHBOARD_PORT` | No | `9876` | Local port for the SSE/HTTP server. |
| `SWITCHBOARD_TIMEOUT_SECONDS` | No | `86400` | How long `ask_human` blocks before returning `__TIMEOUT__`. |
| `SWITCHBOARD_LOG_PATH` | No | `./logs/switchboard.jsonl` | Path to the event audit log. |
| **Android & Firebase** | | | |
| `SWITCHBOARD_ENABLE_ANDROID` | No | `false` | Set to `true` to enable the Firebase backend and Android integration. |
| `FIREBASE_DATABASE_URL` | If enabled | | The URL of your Firebase Realtime Database. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | If enabled | | Absolute path to your Firebase service account key JSON file. |
| `FIREBASE_STORAGE_BUCKET` | No | | Hostname of your Firebase Storage bucket. |

## Wire your agent to it

### AI Agents (Claude, Gemini, etc.)

#### Gemini CLI

```bash
gemini mcp add switchboard http://localhost:9876/mcp --type http --trust
gemini skills link .\skill
```

#### Claude Code

```bash
claude mcp add switchboard --scope user --transport http http://localhost:9876/mcp
# Copy skill/SKILL.md to ~/.claude/skills/switchboard/SKILL.md
```

## Android App

The project includes a native Android app in the `android/` directory.

### Build and Install
1.  **Credentials**: Download `google-services.json` from your Firebase project and place it in `android/app/`.
2.  **Deploy**: Connect your phone via USB or Wi-Fi (with Debugging enabled) and run:
    ```powershell
    .\scripts\install-client.ps1
    ```
    This script builds the debug APK, installs it, and launches the app automatically.
3.  **Alternative**: Open the `android/` folder in Android Studio and deploy to your phone.

### Troubleshooting & Manual Wi-Fi Pairing

If you encounter issues pairing your device over Wi-Fi (e.g., "protocol fault" or the device not appearing in Android Studio), follow these manual steps:

1.  **Restart ADB Server**:
    ```powershell
    adb kill-server
    adb start-server
    ```
2.  **Manual Pairing**:
    On your phone, go to **Developer Options > Wireless Debugging > Pair device with pairing code**. Note the IP address, port, and pairing code.
    ```powershell
    adb pair <IP_ADDRESS>:<PAIRING_PORT>
    # Enter the pairing code when prompted
    ```
3.  **Manual Connection**:
    After successful pairing, look at the **IP address & Port** on the main Wireless Debugging screen (note: this port is usually different from the pairing port).
    ```powershell
    adb connect <IP_ADDRESS>:<CONNECTION_PORT>
    ```
4.  **Common Fixes**:
    - **Toggle Wireless Debugging**: If a pairing attempt fails, turn Wireless Debugging OFF and back ON to reset the ports.
    - **Check VPNs**: Ensure neither your PC nor your phone is connected to a VPN, as this often blocks local ADB discovery.
    - **Forget Old Pairings**: If issues persist, select "Forget all paired devices" in the Wireless Debugging settings and start over.

The app uses Firebase Cloud Messaging (FCM) for instant push notifications and Realtime Database for two-way communication.

## Using

### Away mode

Away mode activates when you tell your agent you're stepping away — any phrasing like *"I'm stepping away"* is sufficient. The agent immediately routes all output through `ask_human` or `notify_human`.

- **`ask_human(question, channel_id, sender?, format?, suggestions?)`** — blocks until you reply.
- **`notify_human(message, channel_id, sender?, format?)`** — fire-and-forget status update.
- **`send_document_human(path, channel_id, sender?, caption?)`** — delivers a file to your phone.
- **`message_and_await_agent(channel_id, sender, message?)`** — collaborative session relay.

To exit away mode, reply *"I'm back"*. The agent will provide a **Welcome Back Summary** of what was accomplished while you were away and then resume normal terminal output.

### Replying to messages

Switchboard correlates your reply to the waiting `ask_human` call via the Android app's reply input at the bottom of the channel tab. Type your answer and tap Send. If the question included suggestion buttons, tap one to reply instantly without typing.

### Bring-your-own collab sessions

Two already-running agents can be paired into a collab channel without spawning. Give both agents the same `channel_id` and tell them to call `message_and_await_agent`. The first agent to call creates the session; the second joins. Call order doesn't matter — the gateway buffers the first message until both agents are enrolled.

Each agent uses its own display name as `sender` (e.g. `"Claude"`, `"Gemini"`), which is naturally unique across different agent types. BYO sessions do not enter away mode unless you explicitly step away.

### Spawning a new agent session

With `SWITCHBOARD_SPAWN_ROOT` configured, you can launch a fresh agent session directly from the Android app. Tap the **spawn** button in the app, choose a project, and enter a prompt.

- **Backend selection:** Choose between **Claude** or **Gemini** using the checkboxes.
- **Collab mode:** Enable both to launch a heterogeneous collaborative session — Switchboard opens two terminal tabs that communicate with each other through the gateway.

**Prerequisites:**

- Set `SWITCHBOARD_SPAWN_ROOT` in `.env` and restart the service.
- Register the `SwitchboardSpawn` scheduled task (one-time, elevated PowerShell):

  ```powershell
  .\scripts\register-spawn-task.ps1
  ```

- The task fires in your interactive desktop session so `claude` and `gemini` are reachable.

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
