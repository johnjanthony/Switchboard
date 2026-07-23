# Switchboard Watchtower

A small Windows taskbar widget that shows, at a glance, how full the **context window** is for each running Claude Code session. It complements quota monitors like CodeZeno's Claude-Code-Usage-Monitor (which track your plan's rolling token allowance) by answering a different question: how close is each live session to auto-compaction?

Part of the Switchboard suite (sibling to Switchboard Operator, the dashboard).

The context-fullness bars read only local Claude Code session transcripts (no network). The optional plan-usage feature additionally reads the OAuth token from `~/.claude/.credentials.json` and calls Anthropic's usage endpoint, and the once-a-day session anchor runs a single headless `claude -p .` turn.

## What it shows

- A mini equalizer pinned over the taskbar: one severity-colored bar per active session (height = context fullness), busiest first.
- The max percentage as text, shown only when at least one session is at or above 50% (below that it stays out of the way; an error shows `!`).
- A hover popup with per-session detail: a live/idle status dot, the working-directory label, a `Model · Window` tag (e.g. `Opus · 1M`), with a `WSL` marker shown in front of the working-directory label for sessions running inside a WSL distro, raw tokens (e.g. `661K / 1.0M`), and the percent.
- Both native Windows sessions and sessions running inside WSL distros.

Severity colors: green below 50%, amber 50 to 80%, red above 80%.

## Usage

- **Hover** the widget to open the detail popup; move away to dismiss it.
- **Drag** the small handle on the left (the cursor turns into a resize arrow over it) to reposition the widget along the taskbar. The position is remembered.
- **Right-click the tray icon** for "Refresh now", "Start with Windows", "Crisp text (ClearType)", "Show plan usage", a "Usage poll interval" submenu (1 minute / 5 minutes / 15 minutes / 1 hour), "Open Switchboard dashboard", and "Quit".
- Autostart is on by default, so the widget returns after a reboot.

## Requirements

- Windows 11.
- .NET 9 SDK to build/run (`dotnet --version` should report 9.x).
- Claude Code, which writes session transcripts under `~/.claude/projects/`.
- WSL is optional; if present, sessions in running distros are surfaced automatically.

All commands run from the `watchtower/` directory (the repo root has no top-level solution).

**Dev run (Debug, for quick iteration):**

```
dotnet run --project src\Switchboard.Watchtower\Switchboard.Watchtower.csproj
```

**Tests** (the pure logic is fully unit-tested):

```
dotnet test
```

### Release build (the widget you actually run)

The day-to-day widget is a portable, self-contained single EXE at `publish\Switchboard.Watchtower.exe`. Build it with:

```
dotnet publish src\Switchboard.Watchtower\Switchboard.Watchtower.csproj -c Release -r win-x64 -p:PublishSingleFile=true --self-contained true -o publish
```

Then launch that EXE directly:

```
publish\Switchboard.Watchtower.exe
```

On first launch it registers itself under `HKCU\...\Run` (autostart is on by default), so after a reboot Windows relaunches this same published EXE.

**To update the running Release widget** (e.g. to pick up a rebuild): quit the running instance first via the tray icon's **Quit** — only one instance runs at a time (enforced by a named mutex), and the running EXE is otherwise locked — then re-run the `dotnet publish` above and relaunch `publish\Switchboard.Watchtower.exe`.

Or run the one-shot deploy script from the `watchtower/` directory, which does all three steps (stop the running instance, publish, relaunch):

```
.\deploy-widget.ps1
```

Pass `-NoLaunch` to stop and publish without relaunching.

## Configuration

Settings live at `%APPDATA%\Switchboard\Watchtower\config.json` and are created on first run. Defaults:

```json
{
  "PollIntervalSeconds": 60,
  "ActiveWindowMinutes": 5,
  "LiveThresholdSeconds": 90,
  "ScanWsl": true,
  "Autostart": true,
  "WidgetX": null,
  "LightThemeOverride": null,
  "ShowQuota": true,
  "QuotaPollMinutes": 5,
  "DailyAnchorEnabled": true,
  "DailyAnchorTime": "07:00",
  "PollAntigravityQuota": true,
  "AntigravityQuotaPollIntervalSeconds": 60,
  "Switchboard": {
    "Enabled": false,
    "StatsUrl": "http://localhost:9876/stats",
    "DashboardUrl": "http://localhost:9876/dashboard",
    "ShowBadge": false
  }
}
```

- `PollIntervalSeconds`: how often the transcripts are re-scanned.
- `ActiveWindowMinutes`: a session is shown if its transcript was written within this window.
- `LiveThresholdSeconds`: within this, a session shows a green "live" dot; otherwise grey "idle".
- `ScanWsl`: scan running WSL distros for sessions.
- `Autostart`: register under the `HKCU\...\Run` key.
- `WidgetX`: remembered screen X of the dragged widget (`null` = auto, left of the tray).
- `LightThemeOverride`: force light (`true`) or dark (`false`) rendering; `null` follows the taskbar theme.
- `ShowQuota`: show the plan-usage block.
- `QuotaPollMinutes`: plan-usage poll cadence (1, 5, 15, or 60).
- `DailyAnchorEnabled`: fire a once-a-day headless turn to start (anchor) your 5-hour session window at a chosen time (default on).
- `DailyAnchorTime`: local `HH:mm` for the daily anchor (default `07:00`); a malformed value falls back to `07:00`.
- `PollAntigravityQuota`: show the Antigravity (agy) model-group quota block (default on; requires the Antigravity IDE running).
- `AntigravityQuotaPollIntervalSeconds`: how often to re-fetch agy quota from the language server (default `60`).
- `Switchboard`: gates the Switchboard/Operator stats line, the dashboard launcher, and an optional tray pending badge.

Errors are logged to `%APPDATA%\Switchboard\Watchtower\log.txt`.

## How it works

- **Scan**: each tick (default 60s, off the UI thread), it enumerates top-level session transcripts `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` (subagent transcripts are ignored) that were modified within the active window. WSL sessions are read over `\\wsl.localhost\<distro>\...` for running distros only (so stopped distros are never woken), skipping system distros like `docker-desktop`.
- **Read**: it tails the transcript (growing the read window as needed for very active sessions) to find the last assistant turn, then sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` for the current context size.
- **Window**: Claude Code transcripts record only the base model id (the `[1m]` 1M-context marker is not persisted), so the window is inferred by model family (Opus and Fable run at 1M here; an explicit `[1m]` is also 1M; everything else defaults to 200K) and then floored up to the smallest standard tier that fits the observed context, so the displayed fullness never exceeds 100%.
- **Placement**: the widget tries to embed itself as a true child window of the taskbar (the same SetParent reparenting CodeZeno's monitor uses), so it moves with the taskbar and needs no per-tick re-raise; if reparenting fails it falls back to a top-most window painted over the taskbar that re-asserts position and top-most about once a second. Either way it repositions when the taskbar moves and re-attaches after an Explorer restart. It is a layered window; by default it renders an opaque taskbar-matching background with crisp ClearType text (the background pixels are masked to near-invisible so it reads as transparent), and a tray toggle switches to true per-pixel-alpha transparency.
- **Plan usage**: when `ShowQuota` is on, it reads the OAuth token from `~/.claude/.credentials.json` and polls Anthropic's usage endpoint for the 5-hour and 7-day windows. It never refreshes the token itself: an expired token just keeps the last-known display, and starting a session window is the daily anchor's job.
- **Daily anchor**: once a day at `DailyAnchorTime` (only when the workstation is awake), it starts your 5-hour session window with one headless `claude -p .` turn, so the day's resets fall on your schedule instead of whenever a session first happened to run. It skips when a window is already open (so it never fires mid-session or rotates the OAuth token under a live session), and it does not wake the machine or catch up a missed time.
- **Antigravity quota**: when `PollAntigravityQuota` is on and the Antigravity IDE is running, it queries the local language server's `RetrieveUserQuotaSummary` RPC over loopback (no credentials, no OAuth - it reuses the running IDE's session) for each model group's weekly and 5-hour limits, and renders them beside the Claude usage in the same "used" framing. The widget shows them as extra bar pairs to the right (each pair separated by a grip bar); the popup stacks them as pills above the Claude Code pill, ordered Antigravity w/ Claude, Antigravity w/ Gemini, Claude Code, each with `5h` and `7d` rows. A group you have not used is hidden, and the whole Antigravity block disappears when the IDE (and its language server) is not running - degrading in lockstep with the Antigravity session rings.

## Project layout

```
src/Switchboard.Watchtower.Core/    pure logic (net9.0): parsing, scanners, window math, config
src/Switchboard.Watchtower/         WinForms app (net9.0-windows): widget, popup, tray, Win32 placement
tests/Switchboard.Watchtower.Core.Tests/   xUnit tests for the Core library
tools/IconGen/                              one-off WinForms tool that renders the app icon (icon.ico)
docs/superpowers/specs/          design spec
```

The Core library has no UI or Win32 dependencies; its WSL access is behind the IDistroLister interface and injected glob/mtime functions, so the logic is unit-tested in isolation. The UI layer is thin and verified by running it.

## Notes and limitations

- Putting a widget on the Windows 11 taskbar has no supported API. The primary path reparents the widget into the taskbar as a true child window; if that fails, the fallback top-most-over-the-taskbar recovery path can briefly be covered when the shell re-raises the taskbar, but it recovers within about a second.
- "Running" is approximated by recent transcript modification time; there is no direct process-liveness signal in the transcripts.
- The 1M-vs-200K window is inferred (see above) because the transcript does not record it. If a 1M model is misreported as 200K, add its family to `ModelWindowMap.WindowFor`.
