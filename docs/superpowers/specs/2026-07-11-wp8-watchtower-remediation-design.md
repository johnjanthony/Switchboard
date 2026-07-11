# WP-8 — Watchtower remediation design

**Date:** 2026-07-11
**Status:** Design approved (2026-07-11); ready for plan.
**Work package:** WP-8 of the 2026-07-08 technical-review remediation (`docs/2026-07-08-technical-review-findings.md`).
**Findings covered:** REV-007, REV-008 (HIGH); REV-301–REV-306 (MEDIUM); plus all four folded REV-307 design observations.
**Grounding basis:** `watchtower/` at `999195e` (WP-7 landed). Read verbatim during brainstorming: `WslDistroLister.cs`, `AppConfig.cs`, `AppHost.cs`, `Program.cs`, `SwitchboardStatsReader.cs`, `ClaudeStatusReader.cs`, `QuotaService.cs`, `TranscriptTitles.cs`, `WidgetWindow.cs`, `Native.cs`, `WslSessionScanner.cs`, `IDistroLister.cs`, and the Core test project. `WidgetSnapshotPusher.cs` is to be read during plan grounding (REV-301 touches it).

## Context

Watchtower is the Windows taskbar widget of the Switchboard suite: a .NET 9 WinForms app (`src/Switchboard.Watchtower/`) over a pure logic library (`src/Switchboard.Watchtower.Core/`), with xUnit tests that cover Core only — the test project cannot reference the WinForms UI assembly. Watchtower scans Windows + WSL Claude transcripts, polls Claude plan quota and the Switchboard server, and pushes ring/quota/status snapshots to the server. It embeds as a true taskbar child window via Win32 `SetParent`.

The review found two HIGH robustness holes (a wedged `wsl.exe` freezing all scanning; a transient config-read failure destroying the config) and six MEDIUM issues spanning HTTP timeout handling, transcript-title accounting, the Win32 embed/hook lifecycle, a process-pipe deadlock, and the absence of a global exception handler. This WP fixes all eight and folds the four cheap, in-theme items from the REV-307 observation bag.

## Decisions (locked in brainstorming)

- **REV-008 recovery = refuse-to-save (preserve).** A present-but-unreadable config is never overwritten. The app runs on in-memory defaults for the session; positions/toggles simply do not persist until the file reads clean again (auto-recovers next launch). Rejected: back-up-then-reset (permanently adopts defaults). All read paths also get read-retry (ride transient AV/backup locks) and every write becomes atomic.
- **REV-007 latch = fixed 2-minute expiry**, extracted to a pure Core `ScanGate` so the expiry/supersede logic is unit-testable. A wedged scan (e.g. a `\\wsl.localhost` SMB stall, which has no timeout) can be superseded by a fresh scan after 2 min; the two may briefly overlap (last-writer-wins on the UI; the orphaned thread is harmless). Rejected: poll-interval-relative expiry; wsl.exe-timeout-only (leaves the SMB freeze path).
- **REV-307 = fold all four** cheap observations: PushSnapshot in-flight latch, LOCATIONCHANGE debounce, extract policy to Core, bound the TranscriptTitles cache.
- **REV-305 also covers `ResolveClaudePath`** (John, 2026-07-11): apply the same async-drain fix to the second `ReadToEnd`-before-`WaitForExit` process call, for consistency, even though its `where.exe` output is tiny.
- **REV-306 uses a shared Core logger** (`WatchtowerLog`) rather than inline logging in `Program`, so `Program.Main`'s handlers and `AppHost`'s existing `LogError`/`LogInfo` share one implementation.

## Design — Core library (unit-testable)

### REV-008 — config preservation (`AppConfig.cs`)

`LoadFrom` splits three cases:

- **Absent** (`!File.Exists`) → `new AppConfig()`, savable (unchanged).
- **Present + readable** → parse and return.
- **Present + unreadable/unparseable** → after retrying transient `IOException`s (small fixed count, short `Thread.Sleep` backoff — this runs at startup, before the message loop, so a brief block is fine), return a config flagged degraded. A `null` deserialize result is also treated as degraded (do not adopt defaults over a file that parsed to nothing).

```csharp
[JsonIgnore] public bool LoadDegraded { get; private set; }

public static AppConfig LoadFrom(string path)
{
	if (!File.Exists(path)) return new AppConfig();          // absent -> defaults, savable
	for (int attempt = 0; attempt <= MaxReadRetries; attempt++)
	{
		try
		{
			var parsed = JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(path), Options);
			return parsed ?? new AppConfig { LoadDegraded = true };
		}
		catch (IOException) when (attempt < MaxReadRetries) { Thread.Sleep(ReadRetryDelayMs); }
		catch { return new AppConfig { LoadDegraded = true }; }   // present but unreadable/unparseable
	}
	return new AppConfig { LoadDegraded = true };                // IO retries exhausted
}
```

`SaveTo` becomes atomic and degraded-guarded, returning whether it wrote:

```csharp
public bool Save() => SaveTo(DefaultPath);

public bool SaveTo(string path)
{
	if (LoadDegraded) return false;                          // never clobber a config we could not read
	Directory.CreateDirectory(Path.GetDirectoryName(path)!);
	var tmp = path + ".tmp";
	File.WriteAllText(tmp, JsonSerializer.Serialize(this, Options));
	File.Move(tmp, path, overwrite: true);                   // atomic replace on same volume; handles first-write
	return true;
}
```

`File.Move(..., overwrite: true)` (MoveFileEx / MOVEFILE_REPLACE_EXISTING) is atomic on one volume and, unlike `File.Replace`, does not require the destination to pre-exist. The flag rides `_config` for the process lifetime, so a bad read is preserved across every later drag/toggle. `AppHost.SafeSaveConfig` logs the skip:

```csharp
void SafeSaveConfig()
{
	try { if (!_config.Save()) LogInfo("config-save", "skipped: config load degraded; not overwriting"); }
	catch (Exception ex) { LogError("config-save", ex); }
}
```

### REV-302 — transcript-title offset (`TranscriptTitles.cs`)

Replace the `StreamReader.ReadLine` loop (which consumes a partial trailing line, then sets `offset = fs.Length`) with byte-accurate advancement: read `[offset..EOF]`, find the last `0x0A`, parse only through it, and advance `offset` by exactly `lastNewline + 1`. Bytes after the last newline (the still-being-appended fragment) are left for the next scan.

```csharp
fs.Seek(offset, SeekOrigin.Begin);
long available = fs.Length - offset;
if (available > 0)
{
	var buf = new byte[available];
	fs.ReadExactly(buf, 0, buf.Length);              // exactly `available` bytes are present
	int lastNl = Array.LastIndexOf(buf, (byte)'\n');
	if (lastNl >= 0)
	{
		foreach (var line in Encoding.UTF8.GetString(buf, 0, lastNl + 1).Split('\n'))
		{
			var trimmed = line.TrimEnd('\r');
			if (trimmed.Length == 0) continue;
			var parsed = TranscriptParser.ParseTitleLine(trimmed);
			if (parsed is null) continue;
			if (parsed.Value.Custom) lastCustom = parsed.Value.Title; else lastAi = parsed.Value.Title;
		}
		offset += lastNl + 1;                        // advance only past consumed, newline-terminated bytes
	}
	// else: no complete line yet -> consume nothing, leave offset
}
```

Splitting on `0x0A` is UTF-8-safe (a newline byte never appears inside a multibyte sequence). The shrink-reset (`fs.Length < offset`) is retained. `available` is bounded per scan (only new bytes), so a single-shot read is fine; the plan will guard the `> int.MaxValue` theoretical case.

### REV-307 (fold) — bound the `TranscriptTitles` cache

The static `Cache` dictionary grows one entry per session key forever. Cap it (~256 entries) with FIFO eviction via an insertion-order queue guarded by the existing `Lock`. Worst case on eviction is one re-read of an evicted session from offset 0.

### REV-007 — scan gate (`ScanGate` new Core type + `AppHost`)

A pure single-flight gate with a fixed 2-minute expiry so a wedged scan can never freeze scanning permanently:

```csharp
public sealed class ScanGate
{
	readonly TimeSpan _expiry;
	readonly object _lock = new();
	DateTime? _enteredUtc;

	public ScanGate(TimeSpan expiry) { _expiry = expiry; }

	public bool TryEnter(DateTime nowUtc)
	{
		lock (_lock)
		{
			if (_enteredUtc is DateTime e && nowUtc - e < _expiry) return false;  // in-flight, not yet stale
			_enteredUtc = nowUtc;                                                 // free, or supersede a wedged scan
			return true;
		}
	}

	public void Exit() { lock (_lock) { _enteredUtc = null; } }
}
```

`AppHost._scanning` (the `volatile bool`) is replaced by a `ScanGate(TimeSpan.FromMinutes(2))`. `Scan()` guards with `if (!_scanGate.TryEnter(DateTime.UtcNow)) return;` and the `ContinueWith` calls `_scanGate.Exit()` in place of `_scanning = false`. Access is UI-thread-affine in practice; the internal lock is cheap defence-in-depth. This closes both the wsl.exe-wedge and the `\\wsl.localhost` SMB-stall freeze: even if a scan thread never returns, the gate frees after 2 min and subsequent scans run.

### REV-007 — bound the `wsl.exe` call (`WslDistroLister.cs`)

`RunWsl` currently does an unbounded `StandardOutput.ReadToEnd()` before `WaitForExit(5000)`. Bound the read and kill the tree on timeout; keep the method synchronous (it runs inside `AppHost.Scan`'s `Task.Run`).

```csharp
using var p = Process.Start(psi)!;
var readTask = p.StandardOutput.ReadToEndAsync();
if (!readTask.Wait(WslTimeoutMs))                 // bounded; was unbounded ReadToEnd
{
	try { p.Kill(entireProcessTree: true); } catch { /* already gone */ }
	return "";
}
p.WaitForExit(WslTimeoutMs);
return readTask.Result;
```

`WslTimeoutMs` stays 5000 (matches the prior `WaitForExit` budget). Not cleanly unit-testable (spawns a real process); `Parse` stays covered; the behaviour is validated by the WSL-stop manual smoke.

### REV-307 (fold) — extract policy to Core (`TrayGauge` new type)

Move the tray-gauge "busiest session" rule out of `AppHost.ApplyToUi` (`AppHost.cs:202-210`) into a pure Core function:

```csharp
public readonly record struct TrayGaugeState(double Max, bool AnyError, Severity MaxSeverity);

public static class TrayGauge
{
	public static TrayGaugeState From(IReadOnlyList<SessionModel> sessions)
	{
		bool anyError = false; double max = 0; Severity sev = Severity.Green;
		foreach (var s in sessions)
		{
			anyError |= s.IsError;
			if (!s.IsError && s.Pct >= max) { max = s.Pct; sev = s.Severity; }
		}
		return new TrayGaugeState(max, anyError, sev);
	}
}
```

`AppHost` calls `var g = TrayGauge.From(sessions); _tray.SetGauge(g.Max, g.AnyError, g.MaxSeverity, light);`. The plan additionally assesses moving the `QuotaStatus` enum + the "Ok vs keep-last-known" decision into a pure Core classifier; if that ripples beyond a clean extraction it stays in the app assembly and is noted, not forced. (`ClaudeStatusReader`'s status mapping already lives in Core via `ClaudeServerStatus.ParseView`.)

### REV-306 — shared logger (`WatchtowerLog` new Core type)

Extract the log-path resolution + safe-append (the bodies duplicated in `AppHost.LogError`/`LogInfo`) into one place so `Program.Main` can log before `AppHost` exists:

```csharp
public static class WatchtowerLog
{
	public static string DefaultLogPath => Path.Combine(Path.GetDirectoryName(AppConfig.DefaultPath)!, "log.txt");
	static readonly object Lock = new();

	public static void Info(string source, string message, string? path = null) => Append(source, message, path);
	public static void Error(string source, Exception ex, string? path = null) => Append(source, $"{ex.GetType().Name}: {ex.Message}", path);

	static void Append(string source, string message, string? path)
	{
		try
		{
			path ??= DefaultLogPath;
			Directory.CreateDirectory(Path.GetDirectoryName(path)!);
			lock (Lock) File.AppendAllText(path, $"{DateTime.Now:s} [{source}] {message}{Environment.NewLine}");
		}
		catch { /* logging must never crash the widget */ }
	}
}
```

`AppHost.LogError`/`LogInfo` become thin forwarders (keeping the `Action<string, Exception>` sinks the readers and `QuotaService` are constructed with). Testable against a temp path.

## Design — App assembly (review + manual smoke)

### REV-301 — HTTP timeout handling

`HttpClient`'s timeout surfaces as a `TaskCanceledException` (an `OperationCanceledException`); the readers currently rethrow it (`catch (OperationCanceledException) { throw; }`), so the task completes `Canceled`, the continuations' `if (t.IsFaulted)` is false, and they fall through to `t.Result` — which throws on a canceled task (stale panel, nothing logged, stray throw onto the UI thread). The token is always `CancellationToken.None`, so cancellation is never legitimate.

Fix: drop the OCE rethrow in `SwitchboardStatsReader.FetchAsync`, `ClaudeStatusReader.GetViewAsync` + `PostActionAsync`, and `WidgetSnapshotPusher.PushAsync`; let the general `catch` return the unavailable sentinel (null / hidden-idle view) or log. The tasks then complete `RanToCompletion` and the existing continuations are safe unchanged. (`QuotaService` already routes timeouts through its general `catch` → `Failed`; no change needed there.)

### REV-303 — WinEvent hook re-registration (`WidgetWindow.AttachToTaskbar`)

The `if (_winEventHook == IntPtr.Zero)` guard means the hook is never re-registered after explorer restarts (the handle stays non-zero though the hook died with explorer's thread). Change `AttachToTaskbar` to always unhook a stale hook, then hook against the (possibly new) taskbar thread:

```csharp
if (_winEventHook != IntPtr.Zero) { Native.UnhookWinEvent(_winEventHook); _winEventHook = IntPtr.Zero; }
if (_taskbar != IntPtr.Zero)
{
	uint thread = Native.GetWindowThreadProcessId(_taskbar, out _);
	_winEventHook = Native.SetWinEventHook(
		Native.EVENT_OBJECT_LOCATIONCHANGE, Native.EVENT_OBJECT_LOCATIONCHANGE,
		IntPtr.Zero, _winEventCb, 0, thread, Native.WINEVENT_OUTOFCONTEXT);
}
```

`AttachToTaskbar` is called on startup, on the `TaskbarCreated` broadcast, and on the child-detach recovery in `KeepOnTop` — all the points where the taskbar thread can change. Add a comment documenting the implicit WinForms child-HWND recreation recovery path (which currently carries explorer-restart recovery, untested/undocumented). Manual smoke: explorer restart.

### REV-304 — `TryEmbed` style rollback (`WidgetWindow.TryEmbed`)

The WS_POPUP→WS_CHILD flip happens before `SetParent`; on failure the window is left a desktop-parented child with wrong overlay semantics. Capture the original `GWL_STYLE` + `GWL_EXSTYLE` first; if `GetParent(Handle) != _taskbar` after `SetParent` (the existing, reliable success test — `SetParent`'s NULL return is ambiguous for a former top-level window), restore both styles and re-apply `SWP_FRAMECHANGED`, leaving `_embedded = false` so the proven overlay fallback behaves correctly. Review-verified plus the happy-path embed smoke; forcing a real `SetParent` failure is impractical.

### REV-305 — drain process pipes (`QuotaService.cs`)

`RefreshViaCli` redirects stdout/stderr and never reads them, then `WaitForExit(30000)` — output over the ~4 KB pipe buffer blocks the child, costing a 30 s stall on every quota poll during an expired-token window. Start async drains before waiting:

```csharp
using var p = Process.Start(psi);
if (p is null) return;
var outTask = p.StandardOutput.ReadToEndAsync();
var errTask = p.StandardError.ReadToEndAsync();
p.StandardInput.Close();
if (!p.WaitForExit(30000)) { try { p.Kill(true); } catch { /* already gone */ } }
```

Apply the same pattern to `ResolveClaudePath` (per John): start the read task(s) before `WaitForExit(5000)`. Its `where.exe` output is tiny (no real deadlock risk), so this is consistency, not a bug fix.

### REV-306 — global exception handlers (`Program.cs`)

Wire both handlers to `WatchtowerLog` before the message loop, so a UI-thread throw logs (and, in `CatchException` mode, the widget stays alive) rather than dying silently:

```csharp
ApplicationConfiguration.Initialize();
Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
Application.ThreadException += (_, e) => WatchtowerLog.Error("ui-thread", e.Exception);
AppDomain.CurrentDomain.UnhandledException += (_, e) =>
{
	if (e.ExceptionObject is Exception ex) WatchtowerLog.Error("appdomain", ex);
};
```

### REV-307 (fold) — PushSnapshot in-flight latch (`AppHost.PushSnapshot`)

`PushSnapshot` has no in-flight guard, so two POSTs can complete out of order and overwrite newer server state with older. Add the guard the other polls use:

```csharp
volatile bool _snapshotPushing;

void PushSnapshot()
{
	if (_snapshotPusher is null || _snapshotPushing) return;
	_snapshotPushing = true;
	var payload = WidgetSnapshotBuilder.Build(_lastSessions, _lastQuota, DateTimeOffset.Now, _lastTitleStates);
	_snapshotPusher.PushAsync(payload, CancellationToken.None).ContinueWith(t =>
	{
		_snapshotPushing = false;
		if (t.IsFaulted) LogError("widget-snapshot-push", t.Exception!);
	}, TaskScheduler.Default);
}
```

The continuation stays on `TaskScheduler.Default` (fire-and-forget, no UI touch); `volatile` covers the flag's cross-thread visibility. A push skipped because the reset has not yet landed is benign and matches the other latches' semantics.

### REV-307 (fold) — LOCATIONCHANGE debounce (`WidgetWindow`)

`EVENT_OBJECT_LOCATIONCHANGE` is scoped to the whole explorer thread, so it fires per tooltip/animation; each currently triggers a full reposition + 32 bpp bitmap rebuild + `UpdateLayeredWindow`. Debounce **only the WinEvent path** (not the content-update path, which must always render) by comparing the taskbar rect against the last one positioned against:

```csharp
Native.RECT _lastTbRect;   // taskbar rect at last reposition

void OnTrayLocationChanged(IntPtr hHook, uint ev, IntPtr hwnd, int idObject, int idChild, uint thread, uint time)
{
	try { if (IsHandleCreated) BeginInvoke(new Action(RepositionIfTaskbarMoved)); }
	catch { /* shutting down */ }
}

void RepositionIfTaskbarMoved()
{
	if (TaskbarLocator.TryGetTaskbarRect(out var tb) && RectEquals(tb, _lastTbRect)) return;  // no move
	PositionOverTaskbar();   // sets _lastTbRect = tb when it runs
}
```

`PositionOverTaskbar` records `_lastTbRect` on each run. Content updates (`UpdateSessions`, `UpdateQuota`, etc.) still call `PositionOverTaskbar` directly and render unconditionally. `RectEquals` compares the four fields (or `Native.RECT` gains value equality).

## Testing

**Core unit tests** (`dotnet test` from `watchtower/`):

- `AppConfigTests`: unparseable present file → `LoadDegraded == true`, `Save()` returns false and leaves the original bytes intact; absent file → defaults and `Save()` writes; atomic round-trip still passes; a degraded config's `SaveTo` is a no-op over an existing file.
- `TranscriptTitleTests`: a partial trailing line (no `\n`) is not consumed and is picked up once its newline arrives; existing incremental/last-wins/custom-outranks cases stay green; cache-bound test (exceed the cap → oldest evicted, later reads still correct).
- `ScanGateTests`: enter when free; blocked while in-flight; supersede after expiry; `Exit` frees immediately.
- `TrayGaugeTests`: busiest-session selection, error precedence (`AnyError`), empty-list default.
- `WatchtowerLogTests`: append to a temp path, info/error formats, never throws on an unwritable path.

**App assembly** has no unit harness (Core tests can't reference it); REV-301/303/304/305/306-wiring, the REV-007 wsl bounding, and the REV-307 latch + debounce rest on opus review of the diff plus manual smoke.

**Manual smoke (John-assisted; tray-quit before each rebuild):**

1. **WSL stop** (REV-007): with the WSL service stopped/wedged, the widget keeps scanning Windows sessions and never permanently freezes; `wsl.exe` is killed on stall; a fresh scan runs within ~2 min even if one wedged.
2. **explorer.exe restart** (REV-303/304): after restart the widget re-embeds and tray move/resize tracking works again; drag-to-move still tracks.
3. **General**: drag persists position across restart; config survives; quota/stats/status poll; no crashes; the log records a deliberately-induced throw (REV-306).

## Conventions & scope

- Agents don't commit; leave changes staged, John commits (no intermediate checkpoints unless asked — the WP-4+ continuous cadence).
- New `.md` files: `unix2dos` to CRLF (sibling specs are CRLF). watchtower `.cs` files are LF (64/66; the only CRLF outliers, `TabTitles.cs` / `TabTitleTests.cs`, are untouched here) — new `.cs` files stay LF (the Write tool emits LF; do NOT `unix2dos` them) and edits preserve LF; verify with `file` (no "CRLF"), `dos2unix` if one slips. **Never** use `git stash/checkout/restore/reset` in an implementer dispatch (flips CRLF→LF under `autocrlf=input`).
- No version bumps. Watchtower is not plugin-facing → no `plugin.json` bump.
- Gate: `dotnet test` from `watchtower/`.

**Out of scope / deferred:**

- **T-226** (away-mode enforcement doesn't cover the `AskUserQuestion` built-in tool) was logged high-priority during this brainstorm; separate fix.
- The un-folded REV-307 observations stay deferred: `DetailPanel.OnPaint` doing child layout during paint + the duplicated height formula; fixed-pixel geometry under PerMonitorV2 DPI; `DetailPanel.ShowAbove`'s bottom-taskbar assumption + unbounded height; a failed scan rendering as "no recent activity" instead of a distinct error state. Several already correspond to existing `watchtower` backlog items (T-210…T-217); no new work here.
