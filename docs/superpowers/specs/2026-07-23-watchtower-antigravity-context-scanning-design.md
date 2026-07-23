# Watchtower Antigravity Context Scanning — Design

- **Date:** 2026-07-23
- **Status:** Approved (Peer review complete by Claude Win / Fable; ready for implementation plan)
- **Component:** Watchtower (`watchtower/`) — WinForms widget + Core library; Switchboard Server integration (`WidgetSnapshot` / RTDB / Operator)
- **Relationship to prior specs:** Extends Watchtower scanning (`2026-06-25-watchtower-data-into-server-design.md`) and builds on Antigravity CLI support (`2026-07-14-antigravity-cli-support-design.md`).

---

## 1. Goal and Motivation

**Goal:** Enable Watchtower to discover active Antigravity CLI (`agy`) and Antigravity IDE agent sessions, parse their transcript logs, calculate estimated context window usage (token count, capacity, utilization %), map Gemini model token windows, render context rings on the Watchtower widget surfaces (taskbar widget, hover popup, tray gauge), and include them in the `WidgetSnapshot` pushed to Switchboard (`POST /widget-snapshot`).

**Motivation:**
With Antigravity established as a first-class agent in Switchboard (per `2026-07-14-antigravity-cli-support-design.md`), John routinely runs both Claude Code and Antigravity CLI / IDE sessions. Currently, Watchtower only scans Claude Code project directories (`.claude/projects/`), leaving Antigravity sessions completely dark on Watchtower's context rings, tray gauge, Operator dashboard, and Android phone client.

Adding Antigravity scanning gives complete ambient visibility across all active agents on the workstation regardless of CLI engine.

---

## 2. Verified Platform Facts & Disk Layout

These facts were verified on this workstation on 2026-07-23:

1. **Transcript Directory Structure:**
   - Windows Antigravity CLI transcripts: `%USERPROFILE%\.gemini\antigravity-cli\brain\<conversationId>\.system_generated\logs\transcript_full.jsonl`
   - Windows Antigravity IDE transcripts: `%USERPROFILE%\.gemini\antigravity-ide\brain\<conversationId>\.system_generated\logs\transcript_full.jsonl`
   - WSL Antigravity transcripts: Out of scope for this initial chunk (Windows CLI & IDE first).
   - `transcript.jsonl` is per-step truncated (e.g. up to 2x undercount due to truncated output/thinking blocks). Therefore, **`transcript_full.jsonl` must be read for full content length estimation**.

2. **Session Identity & Directory Structure:**
   - `<conversationId>` is a standard UUID string (e.g., `50931fbc-9ba4-459e-8435-b8ad0ee1100d`), matching `cli_session_id` in Switchboard's `SessionRegistry`.
   - The file stem of `transcript_full.jsonl` is literal `"transcript_full"`, **NOT** the session UUID. The `SessionId` MUST be derived from the `<conversationId>` parent directory name (3 levels up from `transcript_full.jsonl`: `logs` → `.system_generated` → `<conversationId>`).

3. **Step Structure & Metadata:**
   - `step_index: 0` contains the initial `USER_INPUT` step with `<USER_REQUEST>`, `<ADDITIONAL_METADATA>` (which carries local time and active document), and initial `<USER_SETTINGS_CHANGE>`.
   - Settings changes (e.g., model switches like `Gemini 3.6 Flash (High)` or `Gemini 3.1 Pro (High)`) can occur mid-session; parsing must inspect the **last** `USER_SETTINGS_CHANGE` step in the transcript.
   - `Cwd` does not appear as a top-level metadata field in step 0; it appears inside tool-call arguments (`Cwd: "c:\\Work\\...`) or identity inject messages. `Cwd` extraction is treated as best-effort with an `"Antigravity"` fallback.

4. **Context Token Estimation:**
   - Claude Code transcripts explicitly record Anthropic API token metrics (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`) on every assistant message.
   - Antigravity step transcripts (`transcript_full.jsonl`) store complete step JSON structures without raw API `usage` blocks per line.
   - **Token Calculation Strategy:**
     - **Primary (Transcript Full Content Volume):** Sum character/byte length across steps in `transcript_full.jsonl` using **1 token ≈ 4 characters**.
     - **Note on Precision:** This is explicitly an **estimate** (system prompts/tool schemas are omitted, while compaction checkpoints like `CONVERSATION_HISTORY` can overestimate post-compaction context on very long sessions).

5. **Model Context Windows:**
   - Gemini models (`Gemini 3.1 Pro`, `Gemini 3.6 Flash`, etc.): Assumed **1,000,000 tokens** capacity default.

---

## 3. Core Architecture & Component Changes

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Watchtower Scanning Loop                         │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
        ┌───────────▼───────────┐        ┌───────────▼───────────┐
        │ WindowsSessionScanner │        │AntigravitySessionScan │
        │ (.claude/projects)    │        │ (.gemini/*/brain)     │
        └───────────┬───────────┘        └───────────┬───────────┘
                    │                                │
                    └────────────────┬───────────────┘
                                     │
                         ┌───────────▼───────────┐
                         │   SessionAggregator   │
                         └───────────┬───────────┘
                                     │
                         ┌───────────▼───────────┐
                         │    WidgetSnapshot     │
                         │(POST /widget-snapshot)│
                         └───────────────────────┘
```

### 3.1 `AntigravitySessionScanner.cs` (New)

A static scanner in `Switchboard.Watchtower.Core` responsible for enumerating active Antigravity session transcripts.

- **Paths scanned:**
  - `%USERPROFILE%\.gemini\antigravity-cli\brain\`
  - `%USERPROFILE%\.gemini\antigravity-ide\brain\`
- **Directory resolution:**
  - Resolves roots using `Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)` (avoiding raw `%USERPROFILE%` string non-expansion under `System.Text.Json`).
- **Enumeration logic:**
  - Enumerates directories under `<brainDir>/<uuid>/` and checks `<brainDir>/<uuid>/.system_generated/logs/transcript_full.jsonl`.
  - Filters using `ActiveClassifier.IsActive(mtime, nowUtc, activeWindowMinutes)` or an ID-based retention check `retainIds.Contains(conversationId)`.
  - Deduplicates by `conversationId` (if a session exists in both CLI and IDE brain roots, the one with the newest mtime wins).

### 3.2 `AntigravityTranscriptParser.cs` & `AntigravityUsageReader.cs` (New)

Split into pure parsing (`AntigravityTranscriptParser.cs`) and file-I/O orchestration (`AntigravityUsageReader.cs`).

- **`AntigravityUsageReader.Read(path, nowUtc, liveThresholdSeconds)`**:
  - Derives `SessionId` from directory parent name: `Directory.GetParent(Directory.GetParent(Directory.GetParent(path).FullName).FullName).Name`.
  - Reads `transcript_full.jsonl` using `FileShare.ReadWrite | FileShare.Delete`.
  - On parse error: returns error model with the correct directory `SessionId` (preventing `"transcript"` phantom session IDs).
- **`AntigravityTranscriptParser` (Pure Parsing)**:
  - **`SessionId`**: Extracted from parent directory UUID.
  - **`Cwd`**: Extracted from tool-call args or identity injects; fallback to `"Antigravity"`.
  - **`Model`**: Parsed from the **last** `USER_SETTINGS_CHANGE` step; default `"Gemini 3.1 Pro"`.
  - **`ContextTokens`**: Estimated tokens based on full step content length (~1 token = 4 characters).
  - **`EffectiveWindow`**: Calculated via `ModelWindowMap.EffectiveWindow(model, contextTokens)`.
  - **`Name` / `Title`**: Extracted from step 0 `<USER_REQUEST>` prompt.
  - **`Status`**: `SessionStatus.Live` or `SessionStatus.Idle` derived via `ActiveClassifier.StatusFor(mtime, nowUtc, liveThresholdSeconds)`.

### 3.3 `ModelWindowMap.cs` Extensions

Update `ModelWindowMap.WindowFor(model)` to handle Gemini models cleanly without bare keyword over-matching:

```csharp
public static long WindowFor(string? model)
{
    if (string.IsNullOrEmpty(model)) return DefaultWindow; // 200,000
    if (model.Contains("gemini", StringComparison.OrdinalIgnoreCase)) return LargeWindow; // 1,000,000
    if (model.Contains("[1m]", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
    if (model.Contains("opus", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
    if (model.Contains("fable", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
    return DefaultWindow;
}
```

### 3.4 `ActiveClassifier.cs` Update

Add an ID-based retention check helper `IsRetainedById(string sessionId, IReadOnlySet<string>? retainIds)` so Antigravity session IDs (derived from directory names, not filename stems) are correctly checked against retained session IDs.

### 3.5 `AppConfig.cs` Configuration

- `ScanAntigravity` (bool, default: `true`).
- `AntigravityRoots` resolved dynamically using `Environment.GetFolderPath`.

### 3.6 `SessionAggregator.cs` Integration

Update `SessionAggregator.Collect`:

```csharp
public static List<SessionModel> Collect(
    IEnumerable<string> windowsTranscripts,
    IEnumerable<(string distro, string path)> wslTranscripts,
    IEnumerable<string> antigravityTranscripts,
    DateTime nowUtc,
    int liveThresholdSeconds,
    Action<string, Exception> onError)
{
    var list = new List<SessionModel>();

    foreach (var path in windowsTranscripts)
        TryAddClaude(list, path, null, nowUtc, liveThresholdSeconds, onError);

    foreach (var (distro, path) in wslTranscripts)
        TryAddClaude(list, path, distro, nowUtc, liveThresholdSeconds, onError);

    foreach (var path in antigravityTranscripts)
        TryAddAntigravity(list, path, nowUtc, liveThresholdSeconds, onError);

    list.Sort((a, b) => b.Pct.CompareTo(a.Pct)); // busiest first
    return list;
}
```

---

## 4. End-to-End Data Flow to Switchboard & Operator

1. **Watchtower Poll Loop:** Every 60s (or on manual refresh), Watchtower scans Claude Code + Antigravity session transcripts.
2. **Snapshot Assembly:** Aggregated `SessionModel` records are assembled into a `WidgetSnapshot` payload.
3. **Server Ingest & Discovery:** Watchtower POSTs `WidgetSnapshot` to `http://localhost:9876/widget-snapshot`.
4. **SessionRegistry Enrichment (`SessionRegistry.apply_rings`):**
   - Discovers active Antigravity session IDs and ensures they exist in `SessionRegistry`.
   - Stores `pct`, `model`, `name`, and `title_state` for phone and Operator UI.
   - **Shielding Benefit:** Active ring sightings prevent active Antigravity sessions from being prematurely marked `lost` by silence sweeps (patching agy's absence of `SessionEnd` hooks).
   - **Roster Decision:** Unregistered active IDE sessions are automatically discovered and displayed on the phone/Operator roster, then swept to `lost` after activity ceases and silence thresholds pass.
5. **UI Fan-out:**
   - Watchtower taskbar widget draws context percentage rings for Antigravity sessions.
   - Operator Dashboard (`/dashboard`) receives RTDB updates and renders context rings next to Antigravity session rows.
   - Android Client (`io.github.johnjanthony.switchboard`) renders context rings on session rows.

---

## 5. Edge Cases & Failure Modes

1. **File Locking during active agent turns:** `transcript_full.jsonl` is opened with `FileShare.ReadWrite | FileShare.Delete` and non-blocking reads.
2. **Missing or Corrupted Transcripts:** If a transcript file is malformed or unreadable, `TryAddAntigravity` catches the exception, logs to `WatchtowerLog`, and returns an error `SessionModel` using the extracted directory `SessionId` (avoiding `"transcript"` phantom IDs).
3. **Deduplication:** If an agy session is logged under both `antigravity-cli` and `antigravity-ide`, Watchtower compares `mtime` and retains only the newest record.
4. **Missing `.gemini` directory:** If Antigravity is not installed or the directory does not exist, `AntigravitySessionScanner` returns an empty set cleanly.

---

## 6. Verification Plan

### 6.1 Automated Unit Tests (`Switchboard.Watchtower.Core.Tests`)
- `AntigravitySessionScannerTests`: Verify scanning of mock brain directory structures, directory UUID extraction, mtime filtering, deduplication.
- `AntigravityTranscriptParserTests`: Verify parsing of sample `transcript_full.jsonl` step lines, model extraction (from last `USER_SETTINGS_CHANGE`), title extraction, character-to-token ratio calculation.
- `ActiveClassifierTests`: Test `IsRetainedById` with UUID session IDs.
- `ModelWindowMapTests`: Verify Gemini model window mappings (`Gemini 3.6 Flash` → 1,000,000; `Gemini 3.1 Pro` → 1,000,000).

### 6.2 Manual / Live Verification
1. **Live Scan Check:** Run an active `agy` or Antigravity IDE session. Verify Watchtower detects the session, parses its token usage, and renders its context ring on the taskbar widget.
2. **Widget Snapshot Verification:** Verify `POST /widget-snapshot` includes the Antigravity session with correct `pct` and `context_tokens`.
3. **Operator & Phone Parity:** Verify context rings appear on Operator (`/dashboard`) and Android phone client for Antigravity sessions.

---

## 7. Implementation Steps (Plan of Action)

1. **Core Data Models, Window Mapping & Classifier:**
   - Update `ModelWindowMap.cs` in `Switchboard.Watchtower.Core` to support `"gemini"`.
   - Update `ActiveClassifier.cs` to add `IsRetainedById`.
2. **Parser & Reader Implementation:**
   - Add `AntigravityTranscriptParser.cs` (pure logic) and `AntigravityUsageReader.cs` (I/O & directory `SessionId` derivation) in `Switchboard.Watchtower.Core`.
3. **Scanner Implementation:**
   - Add `AntigravitySessionScanner.cs` in `Switchboard.Watchtower.Core`.
4. **Configuration & Aggregator Integration:**
   - Update `AppConfig.cs` with `ScanAntigravity`.
   - Update `SessionAggregator.cs` to collect Antigravity transcripts alongside Claude Code transcripts.
5. **Unit Testing:**
   - Add `AntigravitySessionScannerTests.cs` and `AntigravityTranscriptParserTests.cs` in `Switchboard.Watchtower.Core.Tests`.
6. **Live Verification:**
   - Deploy Watchtower build via `watchtower/deploy-widget.ps1 -NoLaunch` and verify against live sessions.
