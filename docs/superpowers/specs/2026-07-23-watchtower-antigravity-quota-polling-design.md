# Watchtower Antigravity Quota Polling Design

**Author:** Antigravity (Claude Opus 4.6)
**Revised:** 2026-07-23 by Claude Fable 5 (live-verified the data source and reworked the UI with John)
**Status:** Implemented 2026-07-23 (subagent-driven; tree-only, commit pending). Live widget check passed. See the As-Built at the end.
**Tracking:** T-245

## Problem

Watchtower displays Claude Code account-level quota utilization (5-hour and 7-day rolling windows) by polling Anthropic's OAuth usage endpoint. Now that Watchtower scans Antigravity sessions (T-180), there is no equivalent quota indicator for the Antigravity model groups. Users cannot see how much of their Antigravity quota remains, when it resets, or which groups are exhausted without switching to the IDE / CLI and running `/usage`.

## Background: how Antigravity quota actually works

This section is rewritten from the original draft to match what `RetrieveUserQuotaSummary` and the agy `/usage` TUI actually return (verified live on 2026-07-23), not the per-model model the first draft inferred from the `antigravity-usage` npm package.

Antigravity quota is **grouped**, and each group mirrors Claude's structure with two rolling windows:

- Models are partitioned into **groups** (observed: "Gemini Models" = Gemini Flash + Gemini Pro; "Claude and GPT models" = Claude Opus + Claude Sonnet + GPT-OSS). The set of groups is server-defined and may change; do not hardcode it.
- Each group has a **weekly limit** and a **5-hour limit**. Both are reported as a `remainingFraction` (1.0 = full, 0.0 = exhausted) plus a `resetTime`.
- Quota is consumed **proportionally to token cost**, shared across the models in a group (which is why every model in a group reports the same fraction).
- The 5-hour window smooths aggregate demand; the weekly window is tied to the user's tier.
- Exhaustion surfaces to the agent as `429 RESOURCE_EXHAUSTED`.

This is a direct analog to Claude Code's 5h/7d windows, which is what makes the "three sets of two bars" UI (below) natural.

### Query surfaces evaluated

1. **`/usage` slash command** (agy CLI or IDE): TUI overlay showing, per group, a Weekly Limit and Five Hour Limit bar with a remaining percentage and a refresh countdown. This is the display we mirror.
2. **Language Server Connect RPC** (`language_server_windows_x64.exe`, spawned by the Antigravity IDE): loopback gRPC-Web/Connect endpoint. **This is the chosen data source** — specifically its `RetrieveUserQuotaSummary` method (see below). The `GetUserStatus` method the original draft targeted returns only the 5-hour fraction, no weekly.
3. **Google Cloud Code REST API** (`cloudcode-pa.googleapis.com` / `daily-cloudcode-pa.googleapis.com`): the backend agy polls. **Rejected** — see the "Rejected: cloud REST path" section; the readable-credential variant is not authorized for the quota endpoint.
4. **`antigravity-usage` npm package**: third-party CLI wrapping surfaces 2 and 3. Useful as a protocol reference only.

## Live verification (2026-07-23)

All findings below were confirmed against the running language server and the live Google endpoints on John's workstation.

- **`GetUserStatus` is insufficient.** It returns per-model `quotaInfo.remainingFraction` + `resetTime` only. That fraction is the **5-hour** value (Gemini models all reported `0.176`, reset `19:19:19Z`, exactly matching the `/usage` five-hour bar). It contains **no weekly field**, and it reports `remainingFraction: null` for the Claude/GPT group when that group's 5h bucket is exhausted. It cannot populate a two-window-per-group UI.
- **`RetrieveUserQuotaSummary` is the right method.** It returns the full grouped structure (weekly + 5h per group) that the `/usage` screen shows, verified byte-for-byte against the screenshot. See the response sample in the appendix.
- **The RPC port must be discovered, not read from the command line.** The `--extension_server_port` value (and the other command-line ports) refused the TLS handshake. The port that answered was found by enumerating the PID's **listening** TCP ports (`Get-NetTCPConnection -OwningProcess <pid>`) and probing each. This corrects step 3 of the original Path A.
- **The cloud REST path (`cloudcode-pa`) is a dead end for this data.** The only readable on-disk credential (`~/.gemini/oauth_creds.json`) is a stale gemini-cli token (a month past its access-token expiry, never rewritten despite daily agy use). Its `refresh_token` still refreshes successfully (non-rotating), and reaches the *same Google identity*, but calling `fetchAvailableModels` (the quota endpoint) returns **403 PERMISSION_DENIED** on both the production and `daily-cloudcode-pa` hosts. agy uses its own Antigravity-authorized OAuth client, whose token lives in a DPAPI-encrypted Chromium browser profile, not in a readable file. `loadCodeAssist` succeeds but carries only tier info, no quota.

## Data source decision

**Query `RetrieveUserQuotaSummary` on the local language server over its loopback Connect RPC.**

The core argument is unchanged from the original recommendation, and is now stronger: Watchtower's Antigravity session display already requires the IDE to be running (session transcripts are written by IDE-hosted agent processes). Reading quota from that same IDE's language server means both features degrade together, with **zero credential handling** — no OAuth flow, no stored tokens, no DPAPI extraction, no token refresh. The language server already holds a valid Antigravity-authorized token; `RetrieveUserQuotaSummary` returns the complete grouped 5h+weekly data.

The cloud REST path (originally "Path B") is rejected: it cannot read the quota endpoint with any credential Watchtower can obtain without decrypting agy's browser profile, and it would add OAuth/token-lifecycle complexity to cover a scenario (IDE closed, quota still wanted) that does not arise when there are active Antigravity sessions to display quota alongside. If IDE-independent quota monitoring is ever needed (e.g. a background cron), that is better served by the `antigravity-usage` tool than by building DPAPI credential extraction into a taskbar widget.

## Detailed design

### New types (Core library)

```csharp
public sealed record AntigravityQuotaBucket(
    string Window,              // "5h" | "weekly"
    double RemainingFraction,   // 0.0-1.0 as returned by the server
    DateTime? ResetTimeUtc);

public sealed record AntigravityQuotaGroup(
    string DisplayName,         // e.g. "Gemini Models"
    string? Description,        // e.g. "Models within this group: Gemini Flash, Gemini Pro"
    IReadOnlyList<AntigravityQuotaBucket> Buckets);

public sealed record AntigravityQuotaSummary(
    DateTime FetchedUtc,
    IReadOnlyList<AntigravityQuotaGroup> Groups);
```

`RemainingFraction` is stored as returned. Rendering converts to **used = 1 − remaining** so it feeds the existing used-framing bar and pace computation unchanged (see UI).

### New classes

#### `AntigravityLanguageServerDetector` (Core library)

Pure logic for finding the best language server process and extracting the CSRF token. Unchanged from the original draft **except**: the caller no longer trusts `--extension_server_port` as the RPC port.

```
Input:  IReadOnlyList<(int Pid, string CommandLine)> processes
Output: (int Pid, string CsrfToken)? best
```

- Filters for command lines containing `language_server` and `antigravity`.
- Extracts `--csrf_token` via regex.
- Scores and selects the best candidate (same heuristic as `antigravity-usage`: `+50` for `language_server`/`exa.language_server_pb`, `+20` for `--csrf_token`, `+10` for `--extension_server_port`, `+5` for `lsp`, `+1` for `antigravity`).
- Pure function: no I/O, fully testable.

#### `AntigravityQuotaClient` (Core library)

Discovers the RPC port for the detected PID, sends the Connect RPC, and parses the response.

```
Input:  int pid, string csrfToken, IReadOnlyList<int> listeningPorts
Output: AntigravityQuotaSummary?
```

- **Port discovery:** iterate the PID's listening TCP ports; the correct one answers `POST /exa.language_server_pb.LanguageServerService/GetUnleashData` (or `GetUserStatus`) with a non-404. The command-line `--extension_server_port` is NOT reliable (verified: refused TLS).
- **Call:** `POST https://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary`.
  - Headers: `Content-Type: application/json`, `Accept: application/json`, `Connect-Protocol-Version: 1`, `X-Codeium-Csrf-Token: <token>`.
  - Body: `{"metadata":{"ideName":"antigravity","extensionName":"antigravity","locale":"en"}}`.
  - Accepts self-signed TLS via `ServerCertificateCustomValidationCallback = (_, _, _, _) => true`, scoped to this client's `HttpClient` only.
  - Timeout: 3 seconds (loopback).
- **Parse:** `response.groups[]` → `AntigravityQuotaGroup`; each `buckets[]` entry → `AntigravityQuotaBucket` (map `window`, `remainingFraction`, `resetTime`). Tolerate mixed JSON number/string types.

Port discovery + the CSRF token are supplied by the poller (WMI/`Get-NetTCPConnection` calls are I/O and live in the app layer); the client stays testable with injected ports and a mockable HTTP handler.

#### `AntigravityQuotaPoller` (WinForms app)

Orchestrates process detection and quota fetching on a timer.

- Runs on the existing `AppHost.Scan()` cadence; re-fetches quota every `AntigravityQuotaPollIntervalSeconds` (default 60; quota changes slowly).
- Enumerates processes (`Process.GetProcesses()` + `Win32_Process` WMI for command lines) and each candidate PID's listening ports (`Get-NetTCPConnection` equivalent via `IPGlobalProperties`/`netstat`).
- Passes the process list to `AntigravityLanguageServerDetector`, then the PID + CSRF + ports to `AntigravityQuotaClient`.
- Exposes `AntigravityQuotaSummary? LatestSnapshot`. Null when no language server is running → the whole Antigravity quota block is hidden.

### UI

The design mirrors the **existing Claude quota bars exactly**, in the **used** framing (bar fills as you consume, with the thin pace/elapsed-time ghost). The Antigravity data is `remaining`, so each bucket renders at `used = 1 − RemainingFraction`. Window durations for pace: 5h = 5 hours, weekly = 7 days; window start = `resetTime − duration`, identical to `QuotaPacing`.

Three "sets" of two bars total:

1. **Claude Code account** — existing 5h / 7d set. Unchanged.
2. **Antigravity group 1** (e.g. "Gemini Models") — 5h / weekly.
3. **Antigravity group 2** (e.g. "Claude and GPT models") — 5h / weekly.

Groups are rendered generically from the response, so additional or renamed groups appear automatically.

#### Taskbar widget (`WidgetWindow`)

- Each group is a two-row set (5h top, weekly bottom), mirroring the existing `DrawQuota` / `DrawQuotaRow` (10-segment bar + skinny pace bar).
- The Antigravity sets are placed **side by side** with the Claude set (after it, before the context rings), separated by `QSep`. No labels in the widget.
- The widget **grows wider** with the number of visible sets (approved). Width = Claude block + Σ visible Antigravity group blocks.

#### Hover popup (`DetailPanel`)

- Each Antigravity group is a **group panel** (like the existing Claude plan-usage panel), titled by the group `DisplayName`, containing two window rows drawn by the existing `DrawQuotaWindow` treatment (segmented bar + `% used` + local reset time via `QuotaFormat.FormatResetTime` + pace verdict).
- Panels stack vertically below the Claude plan-usage panel.

#### Visibility rules

- **Antigravity groups:** hidden in **both** surfaces when the group's data is **unavailable** OR the group is **untouched** (used = 0 / remaining = 100%). A group in active use always shows.
- **Claude set:** unchanged from today — shown whenever its data is available, including at 0% used. (The untouched-means-hide rule is agy-specific.)
- **Whole Antigravity block:** hidden when no language server is running (`LatestSnapshot` null), degrading in lockstep with the Antigravity session display.

### Configuration

- `AppConfig.PollAntigravityQuota` (bool, default `true`).
- `AppConfig.AntigravityQuotaPollIntervalSeconds` (int, default `60`).

### Error handling

All errors are non-fatal; Watchtower keeps showing session data without Antigravity quota.

- Process/port detection failure: silent (no quota to show).
- HTTP timeout / connection refused: silent; clear cached snapshot.
- 403 / CSRF rejection: log warning; clear cached snapshot.
- Malformed JSON: log warning; clear cached snapshot.

### Testing strategy

- **`AntigravityLanguageServerDetectorTests`**: synthetic command-line strings — extraction, scoring, edge cases (missing args, multiple candidates, non-antigravity processes).
- **`AntigravityQuotaClientTests`**: mock HTTP responses using the real `RetrieveUserQuotaSummary` sample (appendix) — group/bucket parsing, `remainingFraction`→used conversion, missing weekly bucket, exhausted (0.0) bucket, empty groups, malformed JSON.
- **Rendering unit tests**: used-fraction conversion and hide-rule predicates (agy untouched → hidden; Claude 0% → shown; unavailable → hidden) as pure functions in Core.
- **Integration verification**: manual smoke test against the live language server after implementation, comparing the widget/popup against `/usage`.

### Files

```
watchtower/src/Switchboard.Watchtower.Core/
  AntigravityQuotaModel.cs              [NEW] Bucket/Group/Summary records + hide-rule predicates
  AntigravityLanguageServerDetector.cs  [NEW] Process detection + CSRF extraction (pure)
  AntigravityQuotaClient.cs             [NEW] Port discovery + Connect RPC + response parsing
  AppConfig.cs                          [MODIFY] PollAntigravityQuota, AntigravityQuotaPollIntervalSeconds

watchtower/src/Switchboard.Watchtower/
  AntigravityQuotaPoller.cs             [NEW] Timer-based orchestrator (process/port I/O)
  AppHost.cs                            [MODIFY] Wire up poller
  WidgetWindow.cs                       [MODIFY] Render Antigravity sets side by side
  DetailPanel.cs                        [MODIFY] Render per-group quota panels

watchtower/tests/Switchboard.Watchtower.Core.Tests/
  AntigravityLanguageServerDetectorTests.cs  [NEW]
  AntigravityQuotaClientTests.cs             [NEW]
```

## Appendix: protocol details (verified)

### Chosen endpoint

```
POST https://127.0.0.1:<discovered-port>/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary
Headers:
  Content-Type: application/json
  Accept: application/json
  Connect-Protocol-Version: 1
  X-Codeium-Csrf-Token: <csrf_token from process command line>
Body: {"metadata":{"ideName":"antigravity","extensionName":"antigravity","locale":"en"}}
```

The endpoint uses a self-signed TLS certificate on loopback; certificate validation must be bypassed for this client.

### `RetrieveUserQuotaSummary` sample response (live, redacted)

```jsonc
{
  "response": {
    "groups": [
      {
        "displayName": "Gemini Models",
        "description": "Models within this group: Gemini Flash, Gemini Pro",
        "buckets": [
          { "bucketId": "gemini-weekly", "displayName": "Weekly Limit", "window": "weekly",
            "remainingFraction": 0.75635, "resetTime": "2026-07-28T17:47:29Z" },
          { "bucketId": "gemini-5h", "displayName": "Five Hour Limit", "window": "5h",
            "remainingFraction": 0.1844278, "resetTime": "2026-07-23T19:19:19Z" }
        ]
      },
      {
        "displayName": "Claude and GPT models",
        "description": "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
        "buckets": [
          { "bucketId": "3p-weekly", "displayName": "Weekly Limit", "window": "weekly",
            "remainingFraction": 0.6698312, "resetTime": "2026-07-30T17:40:03Z" },
          { "bucketId": "3p-5h", "displayName": "Five Hour Limit", "window": "5h",
            "remainingFraction": 0.0094936, "resetTime": "2026-07-23T22:40:03Z" }
        ]
      }
    ],
    "description": "Within each group, models share a weekly limit and a 5-hour limit. ..."
  }
}
```

Field notes:
- `window`: `"5h"` or `"weekly"` — the reliable discriminator; do not parse `displayName` for it.
- `remainingFraction`: float 0.0-1.0. `0.0` = exhausted. (`GetUserStatus` reports `null` for an exhausted bucket; `RetrieveUserQuotaSummary` reports a real `0.0`.)
- `resetTime`: ISO 8601 UTC.
- `displayName` / `description`: human strings safe to show; the group heading uses `displayName`.

### Port disambiguation

The language server hosts the Connect RPC on one of the PID's listening ports, which is **not** the `--extension_server_port` from the command line. Discover it by enumerating listening ports for the PID and probing each with a cheap Connect call (`GetUnleashData` or `GetUserStatus`); any non-404 confirms the RPC port.

### CSRF token header name

The header is `X-Codeium-Csrf-Token` (note: `Codeium`, not `Antigravity` — a Codeium-era legacy name preserved in the protocol).

## Rejected: cloud REST path (original "Path B")

Recorded so the rejection is not re-litigated. Querying `cloudcode-pa.googleapis.com` / `daily-cloudcode-pa.googleapis.com` directly:

- The only readable on-disk credential is `~/.gemini/oauth_creds.json`, a **stale gemini-cli token** (not agy's). Its access token was a month expired and never rewritten despite daily agy use — agy authenticates through a DPAPI-encrypted Chromium browser profile (`~/.gemini/antigravity-browser-profile/`), not this file.
- That refresh token **does** still refresh (non-rotating) and reaches the same Google identity, but `fetchAvailableModels` (the quota endpoint) returns **403 PERMISSION_DENIED** — the gemini-cli OAuth client is not authorized for Antigravity's quota surface. `loadCodeAssist` returns 200 but only tier info (it reports the "Gemini Code Assist" product, a different surface from the Antigravity plan).
- Obtaining agy's own authorized token would require decrypting its browser profile (DPAPI + Chromium login-data SQLite + token-rotation handling) — fragile and far more code than the loopback RPC, for a benefit (IDE-independence) that does not arise in practice.

## As-Built (2026-07-23)

Implemented subagent-driven (Profile B: sonnet implementers, opus reviewers), all seven code tasks two-stage-reviewed, whole-branch review "Ready to merge: Yes" (zero Critical/Important). Tree-only, no commits (repo convention; John commits). Watchtower Core suite 201 → 215. Tracking: T-245 (no prior backlog item; spec → plan → SDD direct).

**As designed.** Data source is the language server `RetrieveUserQuotaSummary` Connect RPC over loopback with RPC-port discovery (probing the PID's listening ports, not `--extension_server_port`); generic group/bucket parsing; used framing (`used = 1 − remainingFraction`) reusing the existing Claude bar and pace renderers; agy groups hidden when untouched or when no language server is running; the Claude set unchanged.

**Files.** New — Core `AntigravityQuota.cs` (records, `Parse`, `UsedPercent`, `IsGroupVisible`, `Bucket`, `ToUsedWindow`, `GroupSortKey`), `AntigravityLanguageServerDetector.cs`; app `AntigravityQuotaClient.cs`, `AntigravityQuotaPoller.cs`; tests `AntigravityQuotaTests.cs`, `AntigravityLanguageServerDetectorTests.cs`. Edited — Core `AppConfig.cs` (+`PollAntigravityQuota`, `AntigravityQuotaPollIntervalSeconds`); app `AppHost.cs` (poller wiring mirroring `PollQuota`), `WidgetWindow.cs`, `DetailPanel.cs`; `Switchboard.Watchtower.csproj` (+`System.Management` 10.0.10, needed for `Win32_Process` command lines; the widget runs in the user session so WMI works); `watchtower/README.md`; repo `CLAUDE.md`.

**Deviations / improvements caught in review (beyond the plan's example code):**
- Mixed-type tolerance: a JSON string `remainingFraction` is parsed via `double.TryParse` (invariant) rather than silently defaulting to 0 (= 100% used).
- The widget setter re-anchors on width change (`RecomputeSize(); PositionOverTaskbar();`, matching the sibling setters) — the plan's `Render()`-only code would have let a group appearing/disappearing grow the tray-flush widget into the clock.
- WMI `ManagementObject`/collection handles are disposed (a leak on the 60s poll otherwise).
- The used-window converter was hoisted to Core (`AntigravityQuota.ToUsedWindow`) to de-duplicate it across widget/popup and add a unit test; the client's diagnostic field now emits a no-RPC-port breadcrumb.

**Post-deploy UI refinements (John, at desk, after the live check).** The popup design shifted from titled per-group panels to header-less pills with fully-labelled rows, reordered, and the widget was aligned to match:
- Widget: a grip separator (matching the far-left grab bar) sits between each bar pair.
- Popup: the "Gemini Models" / "Claude and GPT models" pill headers were removed; each row is labelled `5h - <name>` / `7d - <name>`.
- Order (both surfaces, widget left-to-right and popup top-to-bottom): **Antigravity w/ Claude, Antigravity w/ Gemini, Claude Code**. The friendly labels map the server group name (`AntigravityQuota` display helpers in `DetailPanel`), and the shared ordering rule lives in Core `AntigravityQuota.GroupSortKey` (Claude = 0, Gemini = 1, other = 2) so the two surfaces cannot drift.

The generic-rendering property is preserved for bucket data, but the friendly labels and order now recognise the two known groups by name (Gemini / Claude), with a graceful fallback for any unrecognised group. Live widget check passed after these tweaks.
