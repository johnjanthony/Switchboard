# Claude Context Widget — Design Spec

Date: 2026-06-12
Status: Implemented; spec updated post-build to reflect as-shipped behavior

## Summary

A standalone Windows desktop utility that renders the live context-window fullness of each active top-level Claude Code session as a small widget pinned over the taskbar, with a click-to-expand detail panel. It surfaces sessions running both natively on Windows and inside WSL distros. It reads only local Claude Code session transcripts (no API, no credentials) and runs alongside the existing CodeZeno usage monitor, which continues to own plan-quota reporting.

## Motivation

No existing tool fills this niche. The Windows taskbar/tray monitors (CodeZeno, jens-duttke, sr-kai, utajum) report account-wide plan quota only and never read the per-session transcripts. The tools that do compute per-session context-window fullness live on the wrong surface or OS: a Chrome toolbar extension (akakarantzas/claude-code-counter, single session), in-terminal statuslines (ccusage, leeguooooo), or macOS menu-bar apps (Context Manager, TokenEater). The data method is proven — claude-code-counter already reads exactly the JSONL `usage` blocks this spec relies on — it has simply never been put in a Windows taskbar widget with multi-session support. Quota is intentionally out of scope for Phase 1 because CodeZeno already covers it well.

## Goals

- Show, at a glance in the taskbar, how close each running Claude Code session is to context auto-compaction.
- Support multiple concurrent sessions, each as its own bar.
- Surface sessions running inside WSL distros, not just native Windows sessions.
- Let the user drag the widget left/right along the taskbar to a preferred position, persisted across restarts.
- Start automatically with Windows.
- Zero configuration to start; no network calls; no credentials.
- Be a small, owned C# codebase that is easy to extend later (e.g. a Phase 2 quota line).

## Non-goals (Phase 1)

- Plan-quota / rate-limit reporting (deferred to Phase 2; CodeZeno covers it now).
- Subagent / workflow transcripts (only top-level sessions are shown).
- Historical analytics, cost tracking, or charts.
- Running anywhere but Windows (the app is a Windows process; it reads WSL transcripts over the WSL filesystem bridge, but is not itself cross-platform).

## Verified data facts

These were confirmed against real transcripts before locking the design:

- Native Windows top-level session transcripts are `C:\Users\<user>\.claude\projects\<encoded-cwd>\<uuid>.jsonl`. Subagent transcripts live under `.../<uuid>/subagents/agent-*.jsonl` and are ignored.
- WSL transcripts live in the distro's Linux home, reachable from Windows via the filesystem bridge. Verified: distro `Ubuntu-22.04`, home `/home/janthony`, 12 project dirs under `\\wsl.localhost\Ubuntu-22.04\home\janthony\.claude\projects\`. The Linux username (`janthony`) differs from the Windows username (`JohnAnthony`).
- Each assistant message line carries `message.usage` with integer fields `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`.
- Each line carries the real working directory in a top-level `cwd` field (`"cwd":"C:\\Work"` on Windows, `"/home/janthony/work/rpdm"` style in WSL), so labels do not require decoding the mangled folder name.
- Each assistant message carries `message.model`, but transcripts record only the BASE model id (e.g. `claude-opus-4-7`) — the `[1m]` / 1M-context marker is NOT persisted, and there is no `betas` / `context-1m` / `max_tokens` field. The 1M context window therefore cannot be read from the transcript and must be inferred (see Computation rules).

## Computation rules

- Context tokens for a turn = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` (the prompt actually sent; `output_tokens` is excluded). The current context size for a session is this sum from its most recent assistant message.
- Context window size is inferred because the transcript doesn't encode the 1M marker: model family Opus and Fable → 1,000,000 (this user runs both at 1M), an explicit `[1m]` suffix → 1,000,000, everything else (Sonnet, Haiku, unknown) → 200,000 (`ModelWindowMap.WindowFor`). A data-floor then raises the window to the smallest standard tier (200K, then 1M) at least as large as the observed context, since a prompt cannot exceed its own window (`ModelWindowMap.EffectiveWindow`). This guarantees fullness never exceeds 100% and corrects 1M sessions whose recorded model id looks like a 200K model.
- Fullness percent = contextTokens / windowSize.
- Severity color: green < 50%, amber 50–80%, red > 80%.
- Label = the last one or two path segments of the session's `cwd` (works for both Windows and Linux paths). WSL sessions also carry their distro name for display.
- A session is "active" (shown at all) if its transcript mtime is within the last 5 minutes. It is "live" (green status dot) if mtime is within the last ~90 seconds, otherwise "idle" (grey status dot). Live/idle is sampled at tick granularity, so with the default 1-minute tick the distinction resolves to within roughly one tick.

## WSL session support

- Each tick, enumerate only running distros via `wsl --list --running --quiet`, so stopped distros are never woken by the scan.
- Skip system distros (`docker-desktop`, `docker-desktop-data`) and any distro with no discoverable `.claude/projects`.
- For each running distro, glob `\\wsl.localhost\<distro>\home\*\.claude\projects\<proj>\<uuid>.jsonl` (and `\\wsl.localhost\<distro>\root\.claude\projects\...`) so the scan is independent of the Linux username.
- WSL transcripts are the same JSONL format; the same UsageReader/parsing applies. The `cwd` is a Linux path; the label uses its last segments.
- The filesystem bridge (9P over UNC) can be slow, so all scanning runs on a background thread; the UI thread only repaints. The 1-minute default tick keeps bridge traffic low.

## Architecture

The app decomposes into small single-purpose units communicating through plain data records.

```
	WindowsSessionScanner -> enumerates active native top-level <uuid>.jsonl files
	WslSessionScanner     -> enumerates active top-level transcripts in running WSL distros
	UsageReader           -> tails one transcript, parses last assistant line
	SessionModel          -> immutable record: label, distro, pct, tokens, window, model, status, lastActive
	ModelWindowMap        -> model id -> window size
	CwdLabeler            -> cwd string -> friendly label
	WidgetWindow          -> the in-taskbar multi-bar widget (GDI+, Win32 placement, drag)
	DetailPanel           -> the roomy two-line popup
	TrayMenu              -> NotifyIcon + right-click menu (refresh, autostart, settings, quit)
	AppHost               -> polling timer, background scan, config, single-instance, wiring
```

The pure-logic units (UsageReader, ModelWindowMap, CwdLabeler, and the active/live classifier) have no UI, Win32, or WSL dependencies and are unit-tested in isolation. The scanners are thin wrappers over a directory glob (WindowsSessionScanner) or a process-list-plus-glob (WslSessionScanner). The UI units (WidgetWindow, DetailPanel, TrayMenu) are thin and manually verified.

## Data flow

On each timer tick (default 1 minute), AppHost runs the scan on a background task: WindowsSessionScanner and WslSessionScanner produce the active top-level transcripts; for each, UsageReader opens it read-only with `FileShare.ReadWrite`, reads the tail (~64 KB), and parses the last assistant line into a SessionModel (computing tokens, window, pct, severity, status, label, distro). The merged list is sorted busiest-first and marshaled back to the UI thread, which hands it to WidgetWindow (repaints the equalizer; badge = max pct, color = max severity) and, if open, to DetailPanel (repaints rows). Dragging and click-to-expand are handled on the UI thread independently of the tick, so the widget stays responsive between scans.

## Widget rendering (in taskbar)

- A borderless tool window (`WS_EX_TOOLWINDOW`, so it is excluded from Alt-Tab and the taskbar button row), custom-painted with GDI+ into a layered window (`WS_EX_LAYERED` + `UpdateLayeredWindow`). It embeds as a `WS_CHILD` of the taskbar when possible, with a top-most overlay as fallback (see Window placement).
- Content: a mini equalizer — one thin (~6 px) severity-colored vertical bar per active session, height proportional to its fullness percent, ordered busiest-first, plus the max percent rendered as text alongside.
- Theme-aware: reads `HKCU\...\Themes\Personalize\SystemUsesLightTheme` to pick light/dark foreground.
- Rendering: by default the content is painted opaque over a theme-matched background (`#1C1C1C` dark / `#F3F3F3` light) with ClearType sub-pixel text, and background pixels are forced to alpha 1 so the empty area is near-invisible but still hit-testable (the CodeZeno-monitor technique). A tray-menu toggle switches to a fully transparent per-pixel-alpha render instead.
- Draggable: press-and-drag moves the widget horizontally along the taskbar (Y fixed to the taskbar band), clamped to the taskbar and kept left of the tray cluster (it cannot be dragged over the clock); the position is persisted and restored on startup, and re-clamped if the taskbar geometry changes. A press that does not cross a small movement threshold is treated as a click, which toggles the DetailPanel; a press that does cross it is a drag and does not toggle the panel. The TrayMenu (separate NotifyIcon) carries the right-click menu.
- Bar count grows with active sessions; with "top-level sessions only" filtered to the 5-minute window this is normally a handful. Very high counts simply widen the widget (noted as a known consideration, not handled specially in Phase 1).

## Detail panel rendering

- A popup anchored above the widget, dark/light per theme, drop shadow.
- One entry per session, two lines:
	- Line 1: status dot (green live / grey idle) + label + a `Model · Window` tag (e.g. `Opus 4.8 · 1M`); WSL sessions also show their distro (e.g. `WSL · Ubuntu-22.04`).
	- Line 2: severity bar + raw tokens (e.g. `880K / 1.0M`) + percent.
- Sorted busiest-first. Footer notes the refresh cadence.

## Window placement

The widget embeds as a true child of the taskbar, the same technique the CodeZeno usage monitor uses. It locates `Shell_TrayWnd`, switches its style from `WS_POPUP` to `WS_CHILD | WS_CLIPSIBLINGS` (preserving `WS_EX_LAYERED`) via `SetWindowLong` after handle creation, then calls `SetParent(Shell_TrayWnd)`. As a layered child it is DWM-composited above the taskbar's XAML content, so it renders correctly; an earlier non-layered attempt was occluded, which is what made reparenting look impossible (`SetParent` succeeds and `IsWindowVisible` returns true even when occluded, so the failure was easy to misread as fundamental). Positioning is parent-relative (anchored left of `TrayNotifyWnd`, or the user's dragged X) via `MoveWindow`, and the layered surface is pushed with a NULL `pptDst` so `MoveWindow` owns position. A `SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE)` on the taskbar thread repositions it when the tray moves/resizes, and a `GetParent` check on a ~1-second watchdog re-attaches it if the parent is lost (e.g. an Explorer restart, since a `WS_CHILD` does not receive the `TaskbarCreated` broadcast). If `SetParent` fails, the widget falls back to a top-level top-most overlay painted over the taskbar in screen coordinates and re-raised on the watchdog: the previously-shipped behavior, kept as a safety net. The screen-vs-parent-relative coordinate conversion is isolated in `Core/TaskbarPlacement` and unit-tested.

## Configuration and persistence

- Config JSON at `%APPDATA%\ClaudeContextWidget\config.json`: poll interval (default 60 s), active-window minutes (default 5), live threshold seconds (default 90), severity thresholds, light/dark override, widget X position, scan-WSL toggle (default on), autostart flag.
- Autostart: enabled by default. The app registers itself under the `HKCU\...\Run` key on startup and keeps it current with the EXE path; the tray menu toggles it.
- Single-instance enforced via a named mutex.

## Failure behavior

- A single malformed or locked transcript: that session's entry shows `?` for its value, and the error is logged to `%APPDATA%\ClaudeContextWidget\log.txt` (rotating). The widget keeps running.
- A running WSL distro whose bridge path is unreachable or times out: that distro contributes no sessions this tick and the error is logged; the scan continues for other sources. The UI never blocks on the bridge.
- A structural surprise (the `usage` schema changed, or no assistant line is found where one is expected): the widget turns a distinct warning color and surfaces the error in its tooltip. No silent zeros, no `or {}` fallbacks that hide the problem.

## Packaging and build

- .NET 9 (the SDK installed on this machine), WinForms.
- Published as a portable self-contained single EXE: `dotnet publish -c Release -r win-x64 -p:PublishSingleFile=true --self-contained true`.
- Tabs for indentation; CRLF line endings; no version-number edits to any build file unless directed.

## Testing strategy

- TDD the pure core with xUnit against checked-in sample-JSONL fixtures: tail-parse of the last assistant line, token sum, model→window mapping (Opus→1M, `[1m]`→1M, else 200K) and the EffectiveWindow data-floor (observed context > 200K ⇒ ≥1M, never >100%), cwd→label for both Windows and Linux paths, and the active/live classifier (using injected timestamps rather than wall-clock). One test written and run to green at a time.
- The WSL scanner's distro filtering and path-globbing logic is unit-tested with a faked distro list and directory layout; the actual bridge access is manually verified against the real `Ubuntu-22.04`.
- Win32 placement, drag, theming, autostart, and click-to-expand are manually verified against a written checklist (placement over a real taskbar; drag and persist; dark and light themes; multi-session including a WSL session; near-compaction red state; idle vs live dot; relaunch-on-logon).

## Phasing

- Phase 1 (this spec): the context widget and detail panel, context-only, with WSL support, drag-to-position, 1-minute default tick, and autostart.
- Phase 2 (deferred, decide after using Phase 1): an optional quota line computed locally from JSONL (rolling token sum), clearly labeled an estimate, so the tool can stand alone without CodeZeno if desired.

## Risks and open questions

- Win11 taskbar coupling: the widget reparents into `Shell_TrayWnd` as a layered child (visible because it is DWM-composited above the taskbar XAML); a future taskbar rewrite could change `Shell_TrayWnd` / `TrayNotifyWnd` discovery or the layered-child behavior, in which case it falls back to the top-most overlay. Recovery from an Explorer restart while embedded is best-effort (a `GetParent` watchdog re-attach), since a child window does not receive the `TaskbarCreated` broadcast.
- The exact auto-compaction threshold is approximated by the red > 80% band; the real trigger may differ and can be tuned once observed.
- "Running" is approximated by recent mtime; there is no direct process-liveness signal from the transcripts alone. Acceptable for Phase 1.
- WSL bridge latency could make a tick's scan slow; mitigated by scanning only running distros on a background thread at a 1-minute cadence.
- Very large session counts widen the widget; revisit only if it proves annoying in practice.
