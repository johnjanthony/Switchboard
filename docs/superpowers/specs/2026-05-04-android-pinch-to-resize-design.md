---
id: T-010
status: shipped
surfaced: 2026-05-02
authored: 2026-05-04
shipped: 2026-05-04
tags: [client, android]
---

# Android pinch-to-resize text — design

Backlog entry: [`docs/tracking/backlog.md` T-010](../../tracking/backlog.md). Companion ledger row will be added on ship.

## Problem

Code blocks, log snippets, and full-document Markdown can be hard to read at the default font size on a phone — particularly diffs and structured tables. There is no per-device path today other than the system magnifier, which is a graphic-layer zoom that requires panning. Friction is real for code-heavy replies and long shared documents.

## Goal

Pinch gesture in the channel feed and the Markdown viewer **adjusts the rendered font size** of message body / document content. Text reflows at the new size — the existing vertical scroll continues to do the right thing; no panning. Per-surface scale (channel-feed scale separate from viewer scale) persists across app launches per device. No Firebase, no per-channel state.

## Non-goals

- Per-channel scale (overkill — same channel, same person reading, same eyes).
- Per-message scale (would produce a feed of mismatched type sizes; pinches spanning two bubbles would be ambiguous).
- Zooming chrome (sender labels, timestamps, download chip, app bar). Body text only.
- Wear OS or Auto surfaces — phone app only.
- Sync across devices — text size is a per-device ergonomic preference.
- Graphic-layer scaling (`Modifier.scale`, `Modifier.graphicsLayer`). Font-size scaling reflows; graphic scaling forces panning.

## Surfaces

Two independent scales:

| Surface | Composable | Scale key |
|---------|------------|-----------|
| Channel feed | [`SessionViewScreen.kt`](../../../android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionViewScreen.kt) | `font_scale_feed` |
| Markdown viewer | [`MarkdownViewerScreen.kt`](../../../android/app/src/main/java/io/github/johnjanthony/switchboard/ui/MarkdownViewerScreen.kt) | `font_scale_viewer` |

One scale value per surface applies to **all** content in that surface — pinching anywhere in the feed scales every bubble's body text simultaneously; pinching in the viewer scales the whole document.

## Behavior

### Bounds and steps

Min 1.0x (no shrinking below default — the default is already the calibrated comfortable size; pinching down has no use case), max 2.5x. Default 1.0x for both surfaces on first launch.

Step size **0.05** — i.e. valid scales are `1.00, 1.05, 1.10, ..., 2.45, 2.50` (31 steps). At a 14sp base, 0.05 steps map to ~0.7sp differences — effectively continuous to the eye, while still always reproducible (no irrational fractional scales like 1.247x stuck in prefs).

### Gesture mapping

`detectTransformGestures` reports a `zoom` multiplier per gesture frame. Track cumulative scale during the gesture and snap to the nearest 0.05 step on gesture end.

- **Live during gesture:** the rendered text size is the *raw* cumulative scale × base size, clamped to [1.0, 2.5]. Smooth visual feedback as fingers move.
- **On gesture end:** snap the cumulative scale to the nearest 0.05 step (using the standard `+ 0.5 then floor` trick over Float arithmetic). Persist the snapped value.

Rationale: at this granularity, snap and live values are visually indistinguishable — snap is just there to keep persisted values clean. Exact-midpoint behavior (e.g. raw 1.025 between 1.0 and 1.05) is undefined: float precision makes "rounds half-up exactly" impossible for values not representable in IEEE-754, and pinch gestures produce continuous values that effectively never land on a midpoint anyway.

### Coexistence with existing gestures

The channel feed `Box` already has a `pointerInput` for the horizontal-pull-to-reveal-timestamps gesture ([`SessionViewScreen.kt:207`](../../../android/app/src/main/java/io/github/johnjanthony/switchboard/ui/SessionViewScreen.kt#L207)). `detectTransformGestures` only commits when ≥2 pointers are down, so a separate `pointerInput { detectTransformGestures(...) }` block alongside the existing one will not interfere with single-finger drags.

The viewer's `verticalScroll` is a single-pointer gesture; pinch coexists naturally.

LazyColumn item recomposition: changing `fontScale` triggers all visible bubbles to recompose with a new `fontSize` — expected and cheap.

## Implementation

### `MarkdownText` parameter

Add `fontScale: Float = 1f` parameter to [`MarkdownText`](../../../android/app/src/main/java/io/github/johnjanthony/switchboard/MainActivity.kt) (line 306).

- **Markwon path** (the common case — `format == "markdown"`): inside the `AndroidView` factory's `update` block, set `textView.textSize = baseSizeSp * fontScale`. Base size SP is read once from the theme (likely 14sp matching `MaterialTheme.typography.bodyMedium`); confirm during implementation.
- **Plain text path** (the rare fallback): multiply the Compose `fontSize` by `fontScale`.

Render time impact: setting `textSize` re-lays-out the `TextView`; LazyColumn handles this fine on visible items. No proactive layout invalidation needed for off-screen items — they re-measure on scroll-in.

### Scale source

A small `FontScalePrefs` object (or extension functions on `Context`) backed by the existing `switchboard_prefs` `SharedPreferences` file:

```kotlin
fun Context.feedFontScale(): Float =
    getSharedPreferences("switchboard_prefs", MODE_PRIVATE)
        .getFloat("font_scale_feed", 1f)

fun Context.setFeedFontScale(scale: Float) {
    getSharedPreferences("switchboard_prefs", MODE_PRIVATE)
        .edit().putFloat("font_scale_feed", scale).apply()
}
// + viewerFontScale / setViewerFontScale
```

State flow into the composables: hold the scale in a `remember { mutableStateOf(...) }` initialised from prefs at composition; update on gesture end; persist on update. This avoids piping it through `MainViewModel` — the scale is purely view-local UI state.

(MRU pattern in [`MainViewModel.kt:106`](../../../android/shared/src/main/java/io/github/johnjanthony/switchboard/MainViewModel.kt#L106) is the precedent for direct `SharedPreferences` access.)

### Channel feed wiring

In `SessionViewScreen`:

- Add `var feedFontScale by remember { mutableFloatStateOf(context.feedFontScale()) }`.
- Add a second `pointerInput { detectTransformGestures { _, _, zoom, _ -> ... } }` to the existing surrounding `Box`. Track a transient `liveScale` during the gesture; on gesture end, snap to nearest step, write to `feedFontScale`, persist.
- Pass `fontScale = feedFontScale` into the `MarkdownText` invocation inside `MessageBubble`.
- Threading: `MessageBubble` gains a `fontScale: Float` parameter; previews keep their default of 1f.

### Viewer wiring

In `MarkdownViewerScreen`:

- Same shape: `var viewerFontScale by remember { ... }`, `pointerInput` on the scrolling Column, snap-on-end, persist.
- Pass `fontScale = viewerFontScale` to the `MarkdownText` call at line 65.

## Tests

Compose previews at scales `1.0f`, `1.5f`, `2.5f` for:

- `MessageBubble` (one new preview function with the three scales rendered in a Column).
- `MarkdownViewerScreen` (new preview taking a fixed long-markdown sample).

No instrumented test for the gesture itself — Compose pointer testing has high cost and low marginal value here. Manual test plan during implementation:

1. Pinch out in feed → all bubbles enlarge live → release → snaps to the next step → kill app → relaunch → feed still at the new size.
2. Pinch in viewer; verify feed size unchanged.
3. Pinch beyond bounds → clamps at 1.0x / 2.5x.
4. Pinch across two bubbles in the feed → no jank, no per-bubble desync.
5. Pinch while a horizontal-pull-for-timestamp is in progress → both gestures coexist (pinch only commits with second finger down).

## Risks

- **Markwon `textSize` and span sizes.** Markwon renders headers and code blocks at proportional sizes via spans. Setting `textView.textSize` should scale the base; spans are typically sized as a multiple of the base, so they should scale proportionally — but confirm during implementation. If a span uses absolute SP, it'll need the same scale factor applied via Markwon theme.
- **Raw cumulative scale tracking.** `detectTransformGestures` reports per-frame multipliers; the implementation must multiply, not replace. Easy to get wrong.
- **`fillMaxWidth(0.9f)` bubble width.** Larger text may force more wrapping; that's the desired behavior, but watch for any width assumptions in the timestamp row at line 102.

## Open questions resolved

| Question (from backlog) | Resolution |
|--------------------------|------------|
| Min/max bound | 1.0x – 2.5x (no shrinking below default) |
| Step granularity | 0.05 steps, snap on gesture end (visually continuous) |
| Persistence scope | Per-surface (feed + viewer separate), per-app, persisted in `switchboard_prefs` |
| Per-channel scale | Rejected — overkill |

## What ships

- New `fontScale` parameter on `MarkdownText`.
- New helper functions for reading/writing two `SharedPreferences` float keys.
- Two new `pointerInput { detectTransformGestures }` blocks in `SessionViewScreen` and `MarkdownViewerScreen`.
- Threading the scale through `MessageBubble`.
- Three new Compose previews.
- Backlog T-010 → ledger row, journal entry on ship.
