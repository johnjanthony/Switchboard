# Context Rings — Design

**Date:** 2026-06-18
**Component:** Switchboard.Watchtower widget (`WidgetWindow`), with pure layout math in `Switchboard.Watchtower.Core`.

## Goal

Replace the horizontal context equalizer (one vertical bar per session) in the taskbar widget with a single **nested concentric ring cluster**, rendered like the existing taskbar tray gauge. Each session is one ring whose colored arc shows its context fullness; rings nest fullest-on-the-outside.

## Motivation

The equalizer grows horizontally with session count and reads as abstract bars. The tray icon already renders context fullness as a ring gauge (muted track + severity-colored arc). Matching that idiom in the widget makes the two surfaces visually consistent and gives a fixed-width footprint regardless of how many sessions are tracked.

## Current behavior (what we are replacing)

`WidgetWindow.DrawContent` draws the equalizer at `WidgetWindow.cs:355-374`:

- One vertical bar per session, `BarWidth = 6`, `BarGap = 3`.
- Bar height proportional to `s.Pct` (clamped 0..1), error sessions full-height.
- Bar color: `s.IsError ? _palette.Warning : SeverityGradient.For(s.Pct)`.
- A max-% label is drawn to the right (`WidgetWindow.cs:376-395`): `"!"` on error, `"NN%"` when the fullest session is >= 50%, otherwise blank.
- `RecomputeSize` (`WidgetWindow.cs:243-249`) scales widget width by session count via `bars = max(1, count) * (BarWidth + BarGap)` and reserves `RightTextRoom = 52` for the label.

The tray gauge we are matching is `TrayIcon.RenderGauge` (`TrayIcon.cs:117-144`): a muted full-circle track pen, then a `DrawArc` of the severity color starting at `-90f` (12 o'clock) sweeping clockwise by `360 * pct`, round line caps.

## New behavior

### Ring cluster

- Each tracked session becomes one ring: a faint full-circle **track** (`_palette.Track`) with a **colored arc** on top.
- Arc color: `s.IsError ? _palette.Warning : SeverityGradient.For(s.Pct)` (unchanged mapping).
- Arc sweep: error sessions draw a full `360°`; otherwise `360 * clamp(Pct, 0, 1)`, starting at `-90f` and sweeping clockwise, round caps — identical to the tray gauge.
- Rings are **nested concentric**: sorted fullest-first, the fullest session is the **outermost** ring, least-full is innermost.

### Sort order

Descending by a sort key:

- Error sessions sort as "fullest" (rank them at/above `1.0`) so an error always claims an outer ring.
- Non-error sessions sort by `Pct` descending.

Stable for ties is sufficient; no secondary tiebreak is specified.

### Geometry

- Outer diameter `Dmax = min(Height - 8, 28)`, vertically centered in the widget.
- Horizontal placement: cluster left edge at `x = GrabW + quotaW + PadAfterGrab` (same origin the bars use today).
- Ring thickness `2.5px`, inter-ring gap `0.5px`, so the radial step between consecutive rings is `step = thickness + gap = 3px`; each inner ring's bounding rectangle is inset by `step` on every side (the bounding radius shrinks `3px` per ring).
- Pen inset of `thickness/2 + 1` on the outer rect, matching `RenderGauge`, so the outer stroke is not clipped.
- Hard cap `MaxRings = 4`. The effective count is the smaller of `MaxRings` and the number of rings that fit while keeping the innermost ring's bounding radius `>= thickness` (so the innermost ring is never degenerate). In the 34px-tall context-only strip this is ~3 rings (4 in the 44px with-quota strip); exact thickness/cap are parameters of the layout helper, so tuning for legibility is a constant change, not a rewrite.

### Overflow indicator

- If `sessions.Count > visibleRings`, draw small muted (`_palette.Muted`) `+K` text at the top-right corner of the ring cluster's bounding box, where `K = sessions.Count - visibleRings`.
- No `+K` is drawn when all sessions fit.

### Removed

- The max-% number is removed entirely (per-session percents are visible in the popup, making it redundant). The `"!"` error glyph went with it; error is now conveyed solely by a full warning-color ring.
- `RightTextRoom`'s label allowance is removed; only a small right margin remains.

### Sizing

`RecomputeSize` no longer scales with session count:

```text
Width = GrabW + quotaW + PadAfterGrab + (Dmax + OverflowTextRoom) + RightMargin
```

where `OverflowTextRoom` (~14px) reserves space for `+K` and `RightMargin` is a small fixed gap. `Height` is unchanged (`HeightWithQuota = 44` / `HeightContextOnly = 34`).

### Unchanged

- Pending amber badge in the top-right corner.
- Transparent-mode dark halo: arcs and the `+K` text get the same halo treatment the label used, so they stay legible over an unknown taskbar background. ClearType mode draws over the known theme background and needs no halo.
- ClearType vs transparent rendering paths, grab handle, drag behavior.

## Component design

### `ContextRingLayout` (new, in `Switchboard.Watchtower.Core`)

A pure, GDI-free helper that turns the session list + available space into a draw list. Lives in Core so it is covered by the existing unit-test project (`Switchboard.Watchtower.Core.Tests`).

Inputs:

- The session list (or the minimal projection it needs: `Pct` and `IsError` per session).
- Available `Height` (or `Dmax` directly) and the cluster origin `x`.
- Constants: `thickness`, `gap`, `maxRings`.

Output: an ordered list of ring descriptors plus an overflow count. Each ring descriptor carries:

- Bounding rectangle (`RectangleF` or equivalent x/y/w/h) for `DrawEllipse`/`DrawArc`.
- Sweep angle in degrees (`360` for error, else `360 * clamp(Pct,0,1)`).
- `Pct` and `IsError` for the ring. Color selection stays in `WidgetWindow` (it owns the palette): the layout returns these two values and the view computes `IsError ? Warning : SeverityGradient.For(Pct)`. The layout does no color work.

`System.Drawing.RectangleF` is available to Core (it already references `System.Drawing` via `SeverityGradient`), so the helper can return drawing-friendly rects without a custom type, or use a small `readonly record struct` if that reads cleaner.

### `WidgetWindow` changes

- `DrawContent`: replace the equalizer loop and the label block (`WidgetWindow.cs:355-395`) with: call `ContextRingLayout`, iterate the returned rings drawing track + arc (mirroring `RenderGauge`), draw `+K` if overflow, with halo in transparent mode.
- `RecomputeSize`: replace the per-session `bars` width with the fixed cluster width formula above.
- Remove now-unused constants (`BarWidth`, `BarGap`, and the label/`RightTextRoom` sizing) or repurpose them; keep only what the rings need.

## Testing

`ContextRingLayoutTests` in `Switchboard.Watchtower.Core.Tests` (one assertion focus per test, run individually):

- Sort: sessions returned fullest-first; outer ring = highest `Pct`.
- Error ranks outermost: an error session at low `Pct` still gets the outer ring.
- Cap: with more sessions than `maxRings`/fit, only the fittable count produces rings.
- Overflow count: `K` equals `count - visibleRings`, and is `0` when all fit.
- Geometry: ring `i` insets the expected step from ring `i-1`; innermost ring radius stays `>= thickness`.
- Sweep: error → `360`; `Pct` → `360 * Pct`; clamped at `0` and `1`.
- Empty list → no rings, overflow `0`.

GDI drawing in `WidgetWindow` is not unit-tested, consistent with the current code; it is validated by running the widget.

## Out of scope

- No change to how sessions are scanned, aggregated, or to `SeverityGradient` colors.
- No change to the tray gauge, quota rows, popup, or detail panel.
- No new user-facing configuration (ring count cap is a code constant).

## Final rendering decisions (post-implementation visual tuning, 2026-06-18)

Live tuning on the taskbar changed several rendering choices from the original design above. The `ContextRingLayout` helper and its unit tests are unchanged: the helper still defaults to `thickness = 2.5`, `gap = 0.5`, `maxRings = 4`. The widget now passes its own values explicitly, so these are view decisions, not layout-helper changes.

- **No track circle.** The faint full-circle `_palette.Track` ring was dropped; each session is drawn as the colored arc only. The muted gray competed with the color and the empty-track reference reads as clutter at taskbar size.
- **Cap at 3 rings.** `WidgetWindow` passes `maxRings: 3` (const `RingMaxCount`). Fewer rings give each one more radial room; sessions beyond the 3 fullest roll into the `+K` overflow.
- **Thickness 3, gap 0.5.** `WidgetWindow` passes `thickness: 3` (const `RingThickness`) and `gap: 0.5` (const `RingGap`): bolder rings with a minimal separating gap. (Pen width and the layout `thickness` argument are the same const, so they cannot drift.)
- **Rings-only green -> yellow -> red palette.** Rings use a local `RingColor(pct)` (3-stop lerp) instead of `SeverityGradient.For`. The green and red endpoints are identical to `SeverityGradient`, so a near-full context shows the same bright red in the ring and in the popup; the midpoint is a true yellow (rings-only) because the shared orange-amber knee made mid/high bands look alike at this size. Quota bars and the tray icon still use `SeverityGradient` directly.
- **No glow, no saturation bump.** Both were tried and removed: a glow can only be invisible or blur the rings together in a ~26px strip, and a saturation bump pulled the ring color away from the popup's red. Clarity (crisp arcs + distinct hues) won over effects.
