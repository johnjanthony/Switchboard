# Switchboard

> A human-in-the-loop input gateway for AI agents (Claude, Gemini, etc.).

Switchboard is a locally-hosted MCP server that lets AI agents pause mid-task and ask John a question via a native Android app. Designed for away-from-desk workflows where you want your agents to continue working unsupervised until they hit a decision that genuinely requires human input.

## Features

- **Session-id routing**: Each agent session is identified by a `cli_session_id` injected automatically by the PreToolUse hook. Agents only pass `sender` and tool arguments.
- **Conversations**: Messages, members, and state persist in Firebase as named conversations (Active / Ended). At most one is "open" — joinable by any new agent via `enter_conversation`.
- **Asynchronous updates**: Send non-blocking notifications or deliver documents directly to your phone.
- **In-line replies**: View your responses directly in the chat history for full context.
- **Global away mode**: Single server-wide flag. Toggle from the phone's top-bar pill or via the `set_away_mode` MCP tool.
- **Activity indicators**: Prominent high-visibility indicators for unseen activity or pending questions.
- **Session spawning**: Launch fresh agent sessions on your desktop directly from your phone — choose surface (Windows / WSL), project, optional prompt, and whether to create a new conversation or add to an existing one.
- **Conversation composition**: Open + enter, resume dormant sessions, or combine two conversations into one — all from the phone's long-press menu.
- **Rich Markdown**: Full support for bold, italic, code blocks, checklists, and tables.

## Design & Architecture

For an agent-oriented project tour, see [`AGENTS.md`](AGENTS.md). The current design is documented in [`docs/switchboard-design-spec-comprehensive.md`](docs/switchboard-design-spec-comprehensive.md) — covers the Conversation primitive, session-id routing, MCP tool surface, hook plumbing, Firebase schema, spawn (fresh / resume / combine), away mode, hydration, and the Android UI surface.

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
| `SWITCHBOARD_RATE_LIMIT` | No | `30` | Max messages per minute per conversation before `ask_human` / `notify_human` / `send_document_human` are rejected. |
| **Android & Firebase** | | | |
| `FIREBASE_DATABASE_URL` | Yes | | The URL of your Firebase Realtime Database. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | | Absolute path to your Firebase service account key JSON file. |
| `FIREBASE_STORAGE_BUCKET` | No | | Hostname of your Firebase Storage bucket. |
| **Spawn** | | | |
| `SWITCHBOARD_WINDOWS_SPAWN_ROOT` | For spawn | | Windows root containing your project folders (e.g. `C:\Work`). Alias: `SWITCHBOARD_SPAWN_ROOT`. |
| `SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT` | No | `work` | Segment appended to the resolved WSL home to locate the workspace root (e.g. `/home/john/work`). |
| `SWITCHBOARD_WSL_HOME` | No | (auto-detected) | Override for the resolved WSL home path; escape hatch for the NSSM Session 0 case where the `wsl.exe -e bash` probe fails. |

Firebase is mandatory. The server exits at startup with a ConfigError if `FIREBASE_DATABASE_URL` or `FIREBASE_SERVICE_ACCOUNT_JSON` is unset; there is no Android-disabled run mode.

## Wire your agent to it

### AI Agents (Claude, Gemini, etc.)

#### Gemini CLI

```bash
gemini mcp add switchboard http://localhost:9876/mcp --type http --trust
gemini skills link .\skills\switchboard
```

#### Claude Code

Switchboard ships as a Claude Code plugin. From any Claude Code session:

```
/plugin marketplace add C:/Work/switchboard
/plugin install switchboard@switchboard
```

The plugin install wires the skill and the Claude turn-end + agent-status hooks. The MCP server connection is bootstrapped per host by a parallel chezmoi dotfiles effort (Windows uses `localhost:9876`; WSL uses the Windows host IP, resolvable from `/etc/resolv.conf` or `ip route show default | awk '{print $3}'`). If you are not using chezmoi, run `claude mcp add switchboard --scope user --transport http <resolved-url>` per host.

WSL must use bridge networking (NOT mirrored). The Windows server requires `SWITCHBOARD_HOST=0.0.0.0` and a firewall inbound rule for TCP 9876 from the WSL subnet.

## Android App

The project includes a native Android app in the `android/` directory.

### Build and Install
1.  **Credentials**: Download `google-services.json` from your Firebase project and place it in `android/app/`.
2.  **Deploy**: Connect your phone via USB or Wifi (with Debugging enabled) and run:
    ```powershell
    .\scripts\install-client.ps1
    ```
    This script builds the debug APK, installs it, and launches the app automatically.
3.  **Alternative**: Open the `android/` folder in Android Studio and deploy to your phone.

### Troubleshooting & Manual Wifi Pairing

If you encounter issues pairing your device over Wifi (e.g., "protocol fault" or the device not appearing in Android Studio), follow these manual steps:

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

### Pair with Watch

In Android Studio, look at the device dropdown (top center) and select "Pair Devices Using Wi-Fi".

On your watch, in the Wireless debugging screen, tap "Pair new device". You will see a 6-digit pairing code and an IP address.

In Android Studio, select the "Pair using pairing code" tab.

Wait for your watch to appear in the list (it usually shows up as Google Pixel Watch), click it, and enter the 6-digit code shown on the watch.

Once the "Pairing successful" message appears, your watch will show up as a connected device in the target dropdown.

### Troubleshooting Tips

The "Invisible" Watch: If the watch doesn't show up in the pairing list, toggle the Wi-Fi on your watch off and back on. Sometimes the broadcast gets stuck.

Battery Saver: Ensure Battery Saver is off on the watch. It will often kill the ADB process to save juice.

Prompt for Authorization: Keep an eye on your watch face when you first click Run; it will ask you to "Always allow debugging from this computer?" Tap Allow.

## Using

### Away mode

Away mode activates when you tell your agent you're stepping away — any phrasing like *"I'm stepping away"* is sufficient. The agent immediately routes all output through `ask_human` or `notify_human`.

**Human-facing tools:**

- **`ask_human(question, sender, title?, format?, suggestions?)`** — blocks until you reply.
- **`notify_human(message, sender, title?, format?)`** — fire-and-forget status update.
- **`send_document_human(path, sender, title?, caption?)`** — delivers a file to your phone.
- **`set_away_mode(value)`** — toggle the global away-mode flag (agents use this; John can also toggle the phone pill).

**Multi-agent (conversation) tools:**

- **`message_and_await_agent(sender, message, title?)`** — speak to peers in your conversation and block for the next reply.
- **`open_conversation(sender, title?)`** — promote your conversation to the global "open" singleton so other agents can join via `enter_conversation`.
- **`enter_conversation(sender)`** — join the open conversation (or queue for the next intro in your current one) and block until a peer speaks.
- **`combine_conversations(source_id, target_id)`** — merge two conversations; dormant members of `source_id` are migrated and auto-resumed.
- **`lookup_conversation_ids(cwd_filter?, sender_contains?, title_contains?)`** — find conversation IDs to feed `combine_conversations`.
- **`leave_conversation(sender, parting_message)`** — leave the conversation with a final summary; session falls back to its home conversation (away on) or terminal output (away off).

To exit away mode, reply *"I'm back"*. The agent will provide a **Welcome Back Summary** of what was accomplished while you were away and then resume normal terminal output.

### Replying to messages

Switchboard correlates your reply to the waiting `ask_human` call via the Android app's reply input at the bottom of the conversation tab. Type your answer and tap Send. If the question included suggestion buttons, tap one to reply instantly without typing.

### Conversation composition

Multiple agents can share a conversation without spawning. Open one with `open_conversation(sender, title?)`, then have additional agents join via `enter_conversation(sender)`. Agents in the same conversation communicate through `message_and_await_agent`.

You can also merge two existing conversations with `combine_conversations(source_id, target_id)` — dormant members of the source are migrated into the target and revived. All three flows are also available from the phone's long-press menu on any conversation row.

### Spawning a new agent session

With a spawn root configured, you can launch a fresh agent session directly from the Android app. Tap the **+** (spawn) button in the app and fill out the dialog:

- **Surface:** Windows or WSL.
- **Project:** Pick from projects under your configured spawn root for that surface.
- **Prompt:** Optional starting prompt for the agent.
- **Conversation:** Create a new conversation, or add the spawned agent into an existing one.

Spawn auto-enables global away mode if it is currently off; the phone shows a confirmation toast. Claude is the only supported spawn target.

**Prerequisites:**

- Set the spawn root env vars in `.env` and restart the service:
  - `SWITCHBOARD_WINDOWS_SPAWN_ROOT` — Windows root path containing your project folders (e.g. `C:\Work`). Alias: `SWITCHBOARD_SPAWN_ROOT`.
  - `SWITCHBOARD_WSL_SPAWN_ROOT_SEGMENT` — segment appended to the resolved WSL home (default `work`, giving e.g. `/home/john/work`).
- Register the `SwitchboardSpawn` scheduled task (one-time, elevated PowerShell):

  ```powershell
  .\scripts\register-spawn-task.ps1
  ```

- The task fires in your interactive desktop session so `claude` is reachable. The WSL surface additionally requires WSL to be installed and reachable from the same desktop session.

The spawn is audit-logged to `logs/switchboard.jsonl`.

### Formatting messages

`ask_human` and `notify_human` accept an optional `format` parameter. The default is `"plain"`. Pass `format="markdown"` to render the message with Markdown in the Android app — bold, italic, inline code, code blocks, links, checklists, and tables are all supported. Use standard Markdown syntax.

## Manual smoke test

With the server running, the Android app installed, and an agent wired up:

1. Open a Claude Code session and spawn an agent via the Android app (or say *"I'm stepping away"* to an existing session).
2. Ask the agent to do something that should trigger a question, e.g. *"Delete the oldest file in `logs/`."*
3. Watch your phone: you should receive a push notification and see the question appear in the conversation tab.
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

The repo is a monorepo of components: `server/` (the Python MCP), `android/` (the Android + Wear client), and `watchtower/` (the Windows client — Switchboard Watchtower, a .NET 9 taskbar widget).
