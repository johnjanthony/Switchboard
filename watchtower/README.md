# Switchboard Watchtower

A small Windows taskbar widget that shows, at a glance, how full the **context window** is for each running Claude Code session. It complements quota monitors like CodeZeno's Claude-Code-Usage-Monitor (which track your plan's rolling token allowance) by answering a different question: how close is each live session to auto-compaction?

Part of the Switchboard suite (sibling to Switchboard Operator, the dashboard).

Everything it draws comes from the Switchboard server's `GET /stats` (sessions, context and plan usage, which each session's Claude Code mod reports); it reads no transcripts and no credentials. The once-a-day session anchor runs a single headless `claude -p .` turn.

## What it shows

- A mini equalizer pinned over the taskbar: one severity-colored bar per active session (height = context fullness), busiest first.
- The max percentage as text, shown only when at least one session is at or above 50% (below that it stays out of the way; an error shows `!`).
- A hover popup with per-session detail: a live/idle status dot, the working-directory label, a `Model · Window` tag (e.g. `Opus · 1M`), with a `WSL` marker shown in front of the working-directory label for sessions running inside a WSL distro, raw tokens (e.g. `661K / 1.0M`), and the percent.
- Every live session the server knows, on Windows and in WSL.

Severity colors: green below 50%, amber 50 to 80%, red above 80%.

## Usage

- **Hover** the widget to open the detail popup; move away to dismiss it.
- **Drag** the small handle on the left (the cursor turns into a resize arrow over it) to reposition the widget along the taskbar. The position is remembered.
- **Right-click the tray icon** for "Refresh now", "Check Claude status", "Check Antigravity status", "Start with Windows", "Crisp text (ClearType)", "Show plan usage", "Daily wake time", "Open Switchboard dashboard", and "Quit".
- Autostart is on by default, so the widget returns after a reboot.

## Requirements

- Windows 11.
- .NET 9 SDK to build/run (`dotnet --version` should report 9.x).
- A running Switchboard server (sessions and plan usage come from its `/stats`).

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
  "Autostart": true,
  "WidgetX": null,
  "LightThemeOverride": null,
  "ShowQuota": true,
  "DailyAnchorEnabled": true,
  "DailyAnchorTime": "07:00",
  "Switchboard": {
    "Enabled": false,
    "StatsUrl": "http://localhost:9876/stats",
    "DashboardUrl": "http://localhost:9876/dashboard",
    "ShowBadge": false
  }
}
```

- `Autostart`: register under the `HKCU\...\Run` key.
- `WidgetX`: remembered screen X of the dragged widget (`null` = auto, left of the tray).
- `LightThemeOverride`: force light (`true`) or dark (`false`) rendering; `null` follows the taskbar theme.
- `ShowQuota`: show the plan-usage block.
- `DailyAnchorEnabled`: fire a once-a-day headless turn to start (anchor) your 5-hour session window at a chosen time (default on).
- `DailyAnchorTime`: local `HH:mm` for the daily anchor (default `07:00`); a malformed value falls back to `07:00`.
- `Switchboard`: `StatsUrl` is where sessions and plan usage come from, polled every `PollSeconds` (default 4) whether or not `Enabled` is set; `Enabled` gates the stats line, the dashboard launcher and the tray pending badge.

Errors are logged to `%APPDATA%\Switchboard\Watchtower\log.txt`.

## How it works

- **Data**: every `Switchboard.PollSeconds` (default 4 s) it reads the server's `GET /stats`: the live sessions (context tokens and window, model, state) and the account's plan usage, both reported by each session's Claude Code mod. A session counts as live while the server does; it disappears when the session ends. When the server is unreachable the last view stays up and the popup's Switchboard pill reads unavailable.
- **Placement**: the widget tries to embed itself as a true child window of the taskbar (the same SetParent reparenting CodeZeno's monitor uses), so it moves with the taskbar and needs no per-tick re-raise; if reparenting fails it falls back to a top-most window painted over the taskbar that re-asserts position and top-most about once a second. Either way it repositions when the taskbar moves and re-attaches after an Explorer restart. It is a layered window; by default it renders an opaque taskbar-matching background with crisp ClearType text (the background pixels are masked to near-invisible so it reads as transparent), and a tray toggle switches to true per-pixel-alpha transparency.
- **Plan usage**: the 5-hour and 7-day windows as the server last heard them; the server zeroes a window when its reset passes. The popup adds an "as of" line once the reading is over 30 minutes old (it only changes when a session reports one).
- **Daily anchor**: once a day at `DailyAnchorTime` (only when the workstation is awake), it starts your 5-hour session window with one headless `claude -p .` turn, so the day's resets fall on your schedule instead of whenever a session first happened to run. It skips when the server's plan-usage reading shows a window already open (so it never fires mid-session or rotates the OAuth token under a live session), and it does not wake the machine or catch up a missed time.

## Project layout

```
src/Switchboard.Watchtower.Core/    pure logic (net9.0): /stats parsing, session adapter, quota pacing, window math, config
src/Switchboard.Watchtower/         WinForms app (net9.0-windows): widget, popup, tray, Win32 placement
tests/Switchboard.Watchtower.Core.Tests/   xUnit tests for the Core library
tools/IconGen/                              one-off WinForms tool that renders the app icon (icon.ico)
docs/superpowers/specs/          design spec
```

The Core library has no UI or Win32 dependencies, so its logic is unit-tested in isolation. The UI layer is thin and verified by running it.

## Notes and limitations

- Putting a widget on the Windows 11 taskbar has no supported API. The primary path reparents the widget into the taskbar as a true child window; if that fails, the fallback top-most-over-the-taskbar recovery path can briefly be covered when the shell re-raises the taskbar, but it recovers within about a second.
