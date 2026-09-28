# Session model + effort chip (T-251) — design

**Date:** 2026-09-28
**Status:** approved design (brainstorm with John, this session); spec awaiting John's review
**Backlog:** T-251 (current model + effort indicator per session). Builds on T-250 (spawn picks recorded on the session). Related: T-252 (change model/effort on resume, not implemented here), T-261 (agy model-list format drift), T-265 (no Operator component harness), T-273 (why naming lives in one place).

## Goal

Every client surface that lists sessions shows what each session is running, as a short chip such as `Opus 5.5 · xhigh`. The chip shows the model and effort observed in the session's transcript; a spawned session that has not been observed yet shows its spawn pick, visibly muted, so a pick is never passed off as a confirmed value.

## Facts the design rests on (verified 2026-09-28)

- **Transcripts record effort.** Every Claude Code assistant line carries a top-level `effort` string beside `message` (`message.model` holds the model). This session's lines read `xhigh`, matching `/effort xhigh`; an earlier session's lines change from `high` to `xhigh` partway through, so the observed value follows mid-session changes. Values seen: `medium`, `high`, `xhigh`. Haiku lines carry none.
- **Observed model ids.** Across 691 local transcripts the assistant lines use exactly these ids: `claude-opus-5`, `claude-opus-5-5`, `claude-sonnet-5`, `claude-sonnet-5-5`, `claude-fable-5`, `claude-fable-5-1`, `claude-opus-4-8`, `claude-haiku-4-5-20251001`, plus `<synthetic>`.
- **`<synthetic>` lines.** 66 of them, every one with zero `usage`, no `effort`, and the text "No response requested." `TranscriptParser.ParseAssistantLine` accepts them (they carry `message.usage`), `TranscriptTail.LastAssistantLine` returns the last line that parses, and `UsageReader.Read` takes model and usage from that one line. So after a trailing synthetic line the ring reports model `<synthetic>` and 0% context. This is from reading the code, not a live observation.
- **Watchtower covers WSL.** `WslSessionScanner` reads WSL transcripts, so observed values exist for both surfaces.
- **Antigravity default.** `AntigravityTranscriptParser` reports `"Gemini 3.1 Pro"` when the transcript names no model (`lastModel ?? "Gemini 3.1 Pro"`), and that string reaches the ring as the model. When it does find a model it is already a display name.
- **Spawn picks are aliases.** Claude Code picks are `fable`, `opus`, `sonnet`, `haiku` (`server/spawn_catalog.py`); agy picks are `agy models` ids with effort baked in (`gemini-3.6-flash-low`). A blank model pick means the CLI default and can still carry an effort pick.
- **Client state today.** Android `RegistrySession` maps `model` but not `spawn_model` / `spawn_effort`; the phone shows `model` only as the raw id in the session detail sheet's Model row. Operator's `SessionsRail.js` renders `context_pct` but no model. Effort is shown nowhere. Both Android DTOs involved (`RegistrySession`, `WidgetRing`) are `@IgnoreExtraProperties`, so new RTDB keys are safe for an older app.

## Decisions (settled with John)

1. **Observed first, else the spawn pick.** Model and effort resolve as a pair: an observed model brings the observed effort (possibly none); only when no model has been observed does the spawn pair show. Rejected: observed only (blank for spawned sessions until Watchtower sees them), spawn only (stale after `/model` or `/effort`), both-when-different (noisy row).
2. **Family + version naming.** `claude-opus-5-5` reads `Opus 5.5`; an unobserved alias reads `Opus` (the alias resolves at launch, so there is no version to show); unknown ids show raw. Rejected: family only (loses the version), raw ids (long, inconsistent between sources).
3. **The server resolves; clients render.** One resolver under pytest publishes the finished label, so the phone and Operator cannot drift. Rejected: mirrored Kotlin + JS resolvers (the drift T-273 is paying down for `issued_at`), Watchtower-side naming (it never sees spawn picks, so two places would own the name).
4. **Two Watchtower fixes are in scope**, because the chip would otherwise show wrong values as observed: skip `<synthetic>` lines, and stop reporting the Antigravity default as the observed model.
5. **Surfaces:** phone sessions board row, Page B member popover, phone session detail sheet, Operator sessions rail.

## Design

### 1. Watchtower

- `TranscriptParser.ParseAssistantLine` reads the root `effort` string (null when absent or not a string) and returns null for a line whose `message.model` is `<synthetic>`. Returning null makes `TranscriptTail` walk back to the previous real turn, so model, effort and context all come from it.
- `ParsedTurn` gains `Effort`; `SessionModel` gains `Effort`, set by `UsageReader.Read`; everything that copies a `SessionModel` field by field (e.g. `LastKnown`) carries it.
- `WidgetRingDto` gains `[JsonPropertyName("effort")] string? Effort`, filled from the session's `Effort`.
- `AntigravityTranscriptParser` reports a null model when the transcript names none. The `"Gemini 3.1 Pro"` default stays only as the input to `ModelWindowMap.EffectiveWindow`, so the context window is sized as today. Every consumer of `SessionModel.Model` must accept null (the popup's `ShortModel` takes `string?`; the plan confirms each consumer).
- Edge: a transcript whose only assistant lines are synthetic now yields "No assistant turn found", the same error path an empty transcript takes today.

### 2. Server

- **`SessionRecord.effort: str | None`**, commented as the ring-observed effort, the partner of `model`, distinct from `spawn_effort`.
- **`apply_rings` pairing rule.** When a ring carries a non-empty model string, set `rec.model` as today and, if the ring has an `effort` key, set `rec.effort` to its value when it is a non-empty string and to None otherwise. A ring with no `effort` key (a Watchtower older than this change) leaves `rec.effort` untouched. This is the `"key" in ring` test `title_state` already uses. A ring without a model changes neither field.
- **Hydration** reads `effort` like `model`.
- **New module `server/model_label.py`**, pure, no I/O:
  - `friendly_model_name(model_id: str) -> str`
  - `resolve(model, effort, spawn_model, spawn_effort) -> tuple[str | None, str | None]`, returning `(label, source)` with source `"observed"` or `"spawn"`.
- **`SessionRecord.to_payload`** adds `model_label` and `model_source` to the `asdict` result. Hydration builds records field by field, so neither key is ever read back as an input. No new mirror writes: the label changes only when a ring sighting or a spawn record already fires the mirror.

**Naming rules (`friendly_model_name`), first match wins:**

| Input | Rule | Output |
|---|---|---|
| contains whitespace | already a display name (agy observed) | unchanged: `Gemini 3.1 Pro` |
| `claude-<family>-<n>…[-<yyyymmdd>]`, family in fable/opus/sonnet/haiku | drop `claude-` and a trailing 8-digit date; title-case the family; join the number segments with `.` | `Opus 5`, `Opus 5.5`, `Fable 5.1`, `Opus 4.8`, `Haiku 4.5` |
| bare `fable` / `opus` / `sonnet` / `haiku` | title-case | `Opus` |
| `gemini-…` | title-case each `-` segment, keep version segments as written | `gemini-3.8-flash` → `Gemini 3.8 Flash`; `gemini-3.6-flash-low` → `Gemini 3.6 Flash Low` |
| anything else | raw | `xyz-1` |

**Label composition (`resolve`):**

| Observed model | Spawn pick | Label | Source |
|---|---|---|---|
| present, effort `xhigh` | any | `Opus 5.5 · xhigh` | observed |
| present, no effort | any (even with an effort) | `Haiku 4.5` | observed |
| absent | model `opus`, effort `high` | `Opus · high` | spawn |
| absent | model blank, effort `high` | `Default · high` | spawn |
| absent | model `sonnet`, no effort | `Sonnet` | spawn |
| absent | none | none (no chip) | none |

None and the empty string are both "absent" for every input. Effort text is shown as written. The separator is ` · ` (U+00B7 with a space each side).

### 3. Clients

**Android.**
- `RegistrySession` gains `model_label` → `modelLabel: String?` and `model_source` → `modelSource: String?`. The raw `effort`, `spawn_model` and `spawn_effort` stay unmapped: the server has already resolved them.
- `RegistrySessionRow.kt`: the chip goes on the first line after the label and the needs-attention dot, in `labelSmall`; the label gets `weight(1f, fill = false)` so a long name ellipsizes before it pushes the chip off. Normal color when observed, `onSurfaceVariant` when the source is `spawn`. The second line and the right column are unchanged.
- `TabInfoPopover.kt`: each member row becomes sender, chip, context badge. The popover takes the sessions map beside `rings` and looks members up by `cliSessionId`; `MainActivity` passes it.
- `SessionDetailSheet.kt`: the Model row shows `modelLabel`, with ` (spawn pick, not yet observed)` appended when the source is `spawn`, and `-` when there is no label.

**Operator.**
- `derive.js`: pure `sessionModelChip(record)` returns `{text, cls, title}` or null. `cls` is `session-model` plus ` spawn` for a spawn source; `title` is `spawn pick, not yet observed` for a spawn source, else empty.
- `SessionsRail.js`: renders the chip span inside `session-meta`, before the context %.
- `styles.css`: `.session-model`, plus a muted `.session-model.spawn`.

### 4. Testing

Each new test is checked against its counterfactual: it must fail with the change reverted.

- **pytest, new `tests/test_model_label.py`:** the naming table above row by row (all eight observed ids), and every `resolve` row, including that an observed model with no effort does not borrow the spawn effort.
- **pytest, `tests/test_session_registry.py`:** ring model plus effort sets both; model without effort clears a stored effort (the `/model` Opus → Haiku case); a ring without an `effort` key leaves it; a ring without a model changes neither; `to_payload` carries `model_label` / `model_source`; hydration reads `effort` and ignores the derived keys.
- **xUnit (`Switchboard.Watchtower.Core.Tests`):** `ParseAssistantLine` reads `effort`, yields null when absent, and returns null for a `<synthetic>` line; `LastAssistantLine` on a transcript ending in a synthetic line returns the prior real turn; the Antigravity parser reports a null model with no match but the same window as today; `WidgetRingDto` serializes `effort`.
- **node:** `sessionModelChip` for observed, spawn and absent. The rail's rendering stays a manual check (no component harness, T-265).
- **Android:** a DTO mapping test for `model_label` / `model_source`; `:shared:testDebugUnitTest :app:assembleDebug :app:lintDebug :wear:assembleDebug`.

### 5. Deploy and live verification

Order: server, then Watchtower, then phone. The server treats an old Watchtower's missing `effort` key as "leave alone", so it is safe alone; the clients render nothing while `model_label` is absent, so their order does not matter.

1. `restart-service.ps1 -SkipTests` (severs live MCP sessions: ask John first), then `smoke.py --preflight-only`.
2. `watchtower/deploy-widget.ps1`.
3. `.\gradlew.bat :app:installRelease` from `android/`.
4. This session's chip reads `Opus 5.5 · xhigh` in Operator's rail and on the phone's board row, popover and detail sheet.
5. `/effort high` plus one turn: the chip changes to `Opus 5.5 · high`.
6. A session spawned from Operator with a Sonnet/medium pick shows a muted `Sonnet · medium` before its first turn, then `Sonnet 5.5 · medium` at normal weight.
7. `smoke.py --skip-restart`: all flows pass.

### 6. Docs and tracking

- Comprehensive design spec: the `sessions/` record gains `effort`, `model_label`, `model_source`; the widget-snapshot ring gains `effort`; note the `<synthetic>` skip and the Antigravity null model.
- `CLAUDE.md` layout: add `server/model_label.py`.
- At session end: cut T-251 from the backlog, add the PROJECT-JOURNAL entry, and fold the as-built into this spec.

## Out of scope

- Page A conversation rows (multi-member; one chip does not fit), Wear, Operator's conversation detail pane, Watchtower's own popup (keeps `ShortModel`).
- Changing a running session's model or effort (T-252).
- The T-261 `agy models` format drift: an agy spawn id carrying the tab-separated display name would show raw until T-261's parser fix lands.

## Error handling

- Unrecognized model ids render raw rather than being guessed at.
- Malformed ring values (non-string model or effort) are ignored exactly as `apply_rings` ignores a non-string model today.
- No label resolves to no chip; nothing substitutes a placeholder model.
