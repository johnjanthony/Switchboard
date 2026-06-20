# Switchboard Watchtower

A small Windows taskbar widget that shows, at a glance, how full the **context window** is for each running Claude Code session. It complements quota monitors like CodeZeno's Claude-Code-Usage-Monitor (which track your plan's rolling token allowance) by answering a different question: how close is each live session to auto-compaction?

Part of the Switchboard suite (sibling to Switchboard Operator, the dashboard).

It reads only local Claude Code session transcripts. No API calls, no credentials, no network.

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

## Build and run

From the `watchtower/` directory (the repo root has no top-level solution):

```
dotnet run --project src\Switchboard.Watchtower\Switchboard.Watchtower.csproj
```

Run the tests (the pure logic is fully unit-tested):

```
dotnet test
```

Publish a portable, self-contained single EXE:

```
dotnet publish src\Switchboard.Watchtower\Switchboard.Watchtower.csproj -c Release -r win-x64 -p:PublishSingleFile=true --self-contained true -o publish
```

Only one instance runs at a time (enforced by a named mutex). To pick up a rebuild, quit the running instance first (its EXE is otherwise locked).

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
- `Switchboard`: gates the Switchboard/Operator stats line, the dashboard launcher, and an optional tray pending badge.

Errors are logged to `%APPDATA%\Switchboard\Watchtower\log.txt`.

## How it works

- **Scan**: each tick (default 60s, off the UI thread), it enumerates top-level session transcripts `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` (subagent transcripts are ignored) that were modified within the active window. WSL sessions are read over `\\wsl.localhost\<distro>\...` for running distros only (so stopped distros are never woken), skipping system distros like `docker-desktop`.
- **Read**: it tails the transcript (growing the read window as needed for very active sessions) to find the last assistant turn, then sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` for the current context size.
- **Window**: Claude Code transcripts record only the base model id (the `[1m]` 1M-context marker is not persisted), so the window is inferred by model family (Opus and Fable run at 1M here; an explicit `[1m]` is also 1M; everything else defaults to 200K) and then floored up to the smallest standard tier that fits the observed context, so the displayed fullness never exceeds 100%.
- **Placement**: the widget tries to embed itself as a true child window of the taskbar (the same SetParent reparenting CodeZeno's monitor uses), so it moves with the taskbar and needs no per-tick re-raise; if reparenting fails it falls back to a top-most window painted over the taskbar that re-asserts position and top-most about once a second. Either way it repositions when the taskbar moves and re-attaches after an Explorer restart. It is a layered window; by default it renders an opaque taskbar-matching background with crisp ClearType text (the background pixels are masked to near-invisible so it reads as transparent), and a tray toggle switches to true per-pixel-alpha transparency.

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
