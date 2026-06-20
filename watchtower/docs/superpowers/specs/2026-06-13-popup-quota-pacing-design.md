# Popup quota pacing & reset times — design

## Goal

Surface the Claude plan-usage windows (5-hour session, 7-day week) inside the hover popup, with three things per window: how much has been used, whether usage is running ahead of or behind pace, and the exact clock time the window resets. Today these windows appear only as tiny bars on the taskbar strip; the popup shows per-session context usage only.

## Decisions (locked during brainstorming)

- **Layout:** a new "Plan usage" section at the top of the popup, with the existing per-session context rows kept below it unchanged.
- **Pacing visual:** a thin grey "ghost" pace bar beneath each usage bar (brainstorm option B). The usage bar is the existing 10-segment severity-gradient bar; the ghost bar fills to the elapsed-time fraction. Usage bar longer than the ghost bar = burning ahead of pace.
- **Pace tint:** when usage is running ahead of pace, the "time elapsed" caption text is tinted amber (warning). Otherwise neutral/muted. The ghost bar itself stays neutral grey.
- **Reset display:** exact clock time only, 12-hour format, no countdown in the popup (the taskbar strip keeps its countdown).
- **Clock format rule:** same calendar day -> `3:45 PM`; within the next 6 days -> `Fri 3:45 PM`; further out -> `Jun 19, 3:45 PM`.

## Pace model

For a window with a known `ResetsAt` and a fixed duration (5h for the session window, 7d for the weekly window):

- `windowStart = ResetsAt - duration`
- `elapsedFraction = clamp((now - windowStart) / duration, 0, 1)`
- `usageFraction = clamp(Percentage / 100, 0, 1)`
- verdict: `Over` when `usageFraction > elapsedFraction` by a small epsilon, `Under` when below by that epsilon, else `OnPace`.

The ghost bar renders `elapsedFraction`; the usage bar renders `usageFraction`. The verdict drives the caption tint.

The 5h and 7d window starts derive from `ResetsAt` minus the window duration because the Anthropic usage windows reset a fixed interval after they begin. Durations are named constants (`TimeSpan.FromHours(5)`, `TimeSpan.FromDays(7)`).

### Unknown reset time

If `ResetsAt` is null for a window, pace cannot be computed: that row shows the usage bar only — no ghost bar, no reset time, neutral caption (just `NN% used`).

## Components

### Core (pure, unit-tested)

Added to `src/ClaudeContextWidget.Core/Quota.cs`:

- **`QuotaPacing`** — given a `QuotaWindow`, its window duration, and `now`, returns `elapsedFraction` (nullable; null when `ResetsAt` is null) and a `PaceVerdict` (`OnPace` / `Over` / `Under` / `Unknown`). Window durations exposed as named constants.
- **Reset-time formatter** — `FormatResetTime(DateTimeOffset? resetsAt, DateTimeOffset now)` returning the 12-hour string per the clock-format rule above, or empty string when null.

These live next to the existing `QuotaFormat` helpers and follow the same pure-static-function pattern.

### UI

`src/ClaudeContextWidget/DetailPanel.cs`:

- New `UpdateQuota(QuotaUsage usage)` method storing a nullable `QuotaUsage` (mirrors `WidgetWindow.UpdateQuota`). Null until the first successful poll, in which case the quota section is absent and the popup looks exactly as it does today.
- Height calculation grows by the quota section height (one heading line + two window rows) when quota data is present.
- `OnPaint` draws the quota section above the session rows: a "Plan usage" heading, then for each window a label, the exact reset time right-aligned, the 10-segment usage bar (reusing `QuotaFormat.SegmentFill`), the grey ghost pace bar beneath it, and the caption `NN% used · time elapsed NN%` (the "time elapsed" portion tinted amber when over pace).

`src/ClaudeContextWidget/AppHost.cs`:

- In `PollQuota`'s `QuotaStatus.Ok` branch, call `_panel.UpdateQuota(u)` immediately after `_widget.UpdateQuota(u)`.

No new timer: the panel already repaints on each scan tick (via `UpdateSessions` -> `Invalidate`), which is frequent enough for the slow-moving ghost bar and elapsed-time caption.

## Data flow

```
QuotaService.Poll() (background)
  -> AppHost.PollQuota continuation (UI thread)
       -> _widget.UpdateQuota(u)   (existing)
       -> _panel.UpdateQuota(u)    (new)  -> Invalidate -> OnPaint draws quota section
```

## Error / edge handling

- Null `ResetsAt`: usage bar only, no ghost bar, no reset time (see above).
- `now` past `ResetsAt` (stale data between reset and next poll): `elapsedFraction` clamps to 1.
- No quota yet (null `QuotaUsage`): quota section omitted entirely; existing popup behavior preserved.
- All formatting is defensive pure code; logging/crash behavior is unchanged.

## Testing

Pure-function tests alongside `tests/ClaudeContextWidget.Core.Tests/QuotaFormatTests.cs`:

- Pace helper: usage over pace, under pace, exactly on pace (epsilon), null reset -> null fraction / Unknown verdict, clamp when `now` is past reset, clamp when `now` is before window start.
- Reset formatter: same-day, within-6-days (day-prefixed), far-out (month/day), AM/PM boundary, midnight crossing, null -> empty.

Rendering is verified by running the widget and hovering the popup (no rendering unit tests, consistent with the existing codebase).

## Out of scope

- The projection line ("you'll hit the cap ~3:05 PM") from brainstorm option C — not included.
- Any change to the taskbar strip's own quota rows or its countdown.
- Configurability of the clock format (fixed 12-hour per decision).
