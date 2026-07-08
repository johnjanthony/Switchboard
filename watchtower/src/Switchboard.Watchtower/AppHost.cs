using System.Diagnostics;
using System.IO;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class AppHost : IDisposable
{
	readonly AppConfig _config;
	readonly WidgetWindow _widget = new();
	readonly DetailPanel _panel = new();
	readonly TrayIcon _tray;
	readonly System.Windows.Forms.Timer _timer = new();
	readonly System.Windows.Forms.Timer _embedWatchdog = new();
	readonly System.Windows.Forms.Timer _hoverTimer = new();
	readonly System.Windows.Forms.Timer _quotaTimer = new();      // plan-usage poll (1/5/15/60 min)
	readonly System.Windows.Forms.Timer _countdownTimer = new();  // self-rescheduling countdown ticker
	readonly QuotaService _quotaService;
	readonly IDistroLister _distroLister = new WslDistroLister();
	readonly string _logPath;
	volatile bool _scanning;
	volatile bool _quotaScanning;
	readonly System.Windows.Forms.Timer _switchboardTimer = new();
	readonly SwitchboardStatsReader? _switchboardReader;
	volatile bool _switchboardScanning;
	readonly WidgetSnapshotPusher? _snapshotPusher;
	IReadOnlyList<SessionModel> _lastSessions = Array.Empty<SessionModel>();
	QuotaUsage? _lastQuota;
	IReadOnlyDictionary<string, string> _lastTitleStates = new Dictionary<string, string>();
	readonly System.Windows.Forms.Timer _claudeStatusTimer = new();   // steady GET-poll of the server view
	readonly ClaudeStatusReader _claudeStatusReader;
	volatile bool _claudeStatusScanning;
	ClaudeStatusView _claudeStatusView;                               // latest server view (drives the surfaces)
	readonly System.Windows.Forms.Timer _claudePulseTimer = new();

	static int QuotaIntervalMs(int minutes) => Math.Clamp(minutes, 1, 60) * 60_000;

	public AppHost(AppConfig config)
	{
		_config = config;
		_logPath = Path.Combine(Path.GetDirectoryName(AppConfig.DefaultPath)!, "log.txt");
		_quotaService = new QuotaService(m => LogInfo("quota", m), LogError);
		_tray = new TrayIcon(_config.Autostart, _config.ShowQuota, _config.QuotaPollMinutes);

		if (_config.Switchboard.Enabled)
		{
			_switchboardReader = new SwitchboardStatsReader(_config.Switchboard.StatsUrl, LogError);
			_snapshotPusher = new WidgetSnapshotPusher(_config.Switchboard.SnapshotUrl, LogError);
			_switchboardTimer.Interval = Math.Max(2, _config.Switchboard.PollSeconds) * 1000;
			_switchboardTimer.Tick += (_, _) => PollSwitchboard();
		}
		_claudeStatusReader = new ClaudeStatusReader(_config.ClaudeStatus.StatusUrl, LogError);
		_claudeStatusView = ClaudeServerStatus.ParseView("");   // hidden idle until the first poll
		_claudeStatusTimer.Interval = Math.Max(2, _config.ClaudeStatus.PollSeconds) * 1000;
		_claudeStatusTimer.Tick += (_, _) => PollClaudeStatus();
		_claudePulseTimer.Interval = 70;
		_claudePulseTimer.Tick += (_, _) => _widget.TickClaudePulse();
		_tray.ClaudeStatusActionRequested += OnClaudeStatusAction;
		_panel.ClaudeStatusButtonClicked += OnClaudeStatusAction;
		_tray.OpenDashboardRequested += () => OpenDashboard();
		_panel.OpenDashboardRequested += () => OpenDashboard();

		_widget.PreferredX = _config.WidgetX;
		_widget.PositionChanged += x => { _config.WidgetX = x; SafeSaveConfig(); };
		_widget.Diagnostic = m => LogInfo("embed", m);

		_tray.RefreshRequested += Scan;
		_tray.RenderModeToggled += clearType => _widget.SetRenderMode(clearType);
		_tray.QuotaShowToggled += SetQuotaEnabled;
		_tray.QuotaIntervalChanged += minutes =>
		{
			_config.QuotaPollMinutes = minutes;
			_quotaTimer.Interval = QuotaIntervalMs(minutes);
			SafeSaveConfig();
		};
		_tray.AutostartToggled += on =>
		{
			_config.Autostart = on;
			Autostart.Apply(on, Application.ExecutablePath);
			SafeSaveConfig();
		};
		_tray.QuitRequested += () => Application.Exit();

		_timer.Interval = Math.Max(5, _config.PollIntervalSeconds) * 1000;
		_timer.Tick += (_, _) => Scan();

		_embedWatchdog.Interval = 1000;
		_embedWatchdog.Tick += (_, _) => _widget.KeepOnTop();

		_hoverTimer.Interval = 200;
		_hoverTimer.Tick += (_, _) => UpdateHover();

		_quotaTimer.Interval = QuotaIntervalMs(_config.QuotaPollMinutes);
		_quotaTimer.Tick += (_, _) => PollQuota();
		_countdownTimer.Tick += (_, _) => { _widget.RefreshQuotaCountdown(); ScheduleCountdown(); };
	}

	public void Start()
	{
		Autostart.Apply(_config.Autostart, Application.ExecutablePath);
		_widget.Show();
		_widget.AttachToTaskbar();
		_timer.Start();
		_embedWatchdog.Start();
		_hoverTimer.Start();
		_widget.SetShowQuota(_config.ShowQuota);
		if (_config.ShowQuota) { _quotaTimer.Start(); PollQuota(); }
		Scan();
		_panel.UpdateSwitchboard(_config.Switchboard.Enabled, null);
		if (_config.Switchboard.Enabled) { _switchboardTimer.Start(); PollSwitchboard(); }
		_claudeStatusTimer.Start();
		PollClaudeStatus();
	}

	// Tooltip-style: show the panel while the cursor is over the widget (or the panel itself), hide otherwise.
	void UpdateHover()
	{
		var p = Cursor.Position;
		// Use the widget's true screen rect: Form.Bounds is parent-relative once embedded in the taskbar.
		bool over = _widget.ScreenBounds.Contains(p) || (_panel.Visible && _panel.Bounds.Contains(p));
		if (over)
		{
			if (!_panel.Visible) _panel.ShowAbove(_widget.ScreenBounds);
		}
		else if (_panel.Visible)
		{
			_panel.Hide();
		}
	}

	// Fire-and-forget background scan; results marshaled back to the UI thread.
	void Scan()
	{
		if (_scanning) return;
		_scanning = true;
		var now = DateTime.UtcNow;
		var cfg = _config;

		Task.Run(() =>
		{
			List<SessionModel> result;
			DateTime? lastActivityUtc = null;
			try
			{
				var win = WindowsSessionScanner.ActiveTranscripts(WindowsSessionScanner.DefaultProjectsRoot, now, cfg.ActiveWindowMinutes);
				IEnumerable<(string, string)> wsl = Array.Empty<(string, string)>();
				if (cfg.ScanWsl)
				{
					wsl = WslSessionScanner.ActiveTranscripts(_distroLister, now, cfg.ActiveWindowMinutes, WslSessionScanner.DefaultGlob);
				}
				result = SessionAggregator.Collect(win, wsl, now, cfg.LiveThresholdSeconds, LogError);

				// When nothing is active, find the newest transcript so the popup can say "last active agent N ago".
				if (result.Count == 0)
				{
					lastActivityUtc = WindowsSessionScanner.MostRecentActivityUtc(WindowsSessionScanner.DefaultProjectsRoot);
					if (cfg.ScanWsl)
					{
						var wslLast = WslSessionScanner.MostRecentActivityUtc(_distroLister, WslSessionScanner.DefaultGlob);
						if (wslLast is DateTime w && (lastActivityUtc is null || w > lastActivityUtc)) lastActivityUtc = w;
					}
				}
			}
			catch (Exception ex)
			{
				LogError("scan", ex);
				result = new List<SessionModel>();
			}

			// Best-effort tab-title correlation; only worth the UIA walk when there's somewhere to push it.
			// A malformed title (e.g. a lone surrogate that trips TabTitles.Classify) must never fault the
			// scan and freeze the widget - fall back to no verdict.
			IReadOnlyDictionary<string, string> titleStates = new Dictionary<string, string>();
			if (_snapshotPusher is not null)
			{
				try
				{
					titleStates = TabTitles.Correlate(TerminalTabScanner.ReadTabTitles().Select(TabTitles.Classify).ToList(), result);
				}
				catch (Exception ex)
				{
					LogError("title-scan", ex);
				}
			}

			return (result, lastActivityUtc, titleStates);
		}).ContinueWith(t =>
		{
			_scanning = false;
			if (t.IsFaulted) { LogError("scan-continuation", t.Exception!); return; }
			ApplyToUi(t.Result.Item1, t.Result.Item2, t.Result.Item3);
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	void ApplyToUi(IReadOnlyList<SessionModel> sessions, DateTime? lastActivityUtc, IReadOnlyDictionary<string, string> titleStates)
	{
		bool light = _config.LightThemeOverride ?? ThemeReader.IsLightTaskbar();
		_widget.UpdateSessions(sessions, light);
		_panel.UpdateSessions(sessions, light, lastActivityUtc);

		// Tray ring gauge mirrors the busiest session (same rule the widget's % label uses).
		bool anyError = false;
		double max = 0;
		Severity maxSev = Severity.Green;
		foreach (var s in sessions)
		{
			anyError |= s.IsError;
			if (!s.IsError && s.Pct >= max) { max = s.Pct; maxSev = s.Severity; }
		}
		_tray.SetGauge(max, anyError, maxSev, light);

		_lastSessions = sessions;
		_lastTitleStates = titleStates;
		PushSnapshot();
	}

	void LogError(string source, Exception ex)
	{
		try
		{
			Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
			File.AppendAllText(_logPath, $"{DateTime.Now:s} [{source}] {ex.GetType().Name}: {ex.Message}{Environment.NewLine}");
		}
		catch { /* logging must never crash the widget */ }
	}

	// Fire-and-forget quota poll; result marshaled back to the UI thread. Keeps last-known data on
	// any non-Ok status (rate-limited / auth / failure) and simply waits for the next interval.
	void PollQuota()
	{
		if (_quotaScanning) return;
		_quotaScanning = true;
		Task.Run(() => _quotaService.Poll()).ContinueWith(t =>
		{
			_quotaScanning = false;
			if (t.IsFaulted) { LogError("quota-poll", t.Exception!); return; }
			var r = t.Result;
			if (r.Status == QuotaStatus.Ok && r.Usage is QuotaUsage u)
			{
				_widget.UpdateQuota(u);
				_panel.UpdateQuota(u);
				ScheduleCountdown();
				_lastQuota = u;
				PushSnapshot();
				LogInfo("quota", $"ok 5h={u.Session.Percentage:0}% 7d={u.Weekly.Percentage:0}%");
			}
			else
			{
				LogInfo("quota", $"poll status={r.Status} (keeping last-known data)");
			}
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	// Fire-and-forget /stats poll; result marshaled back to the UI thread. Null result == unavailable.
	void PollSwitchboard()
	{
		if (_switchboardReader is null || _switchboardScanning) return;
		_switchboardScanning = true;
		_switchboardReader.FetchAsync(CancellationToken.None).ContinueWith(t =>
		{
			_switchboardScanning = false;
			if (t.IsFaulted) { LogError("switchboard-poll", t.Exception!); return; }
			var stats = t.Result;
			_panel.UpdateSwitchboard(enabled: true, stats);
			_tray.SetPending(_config.Switchboard.ShowBadge, stats is { PendingCount: > 0 });
			_widget.SetPending(_config.Switchboard.ShowBadge, stats is { PendingCount: > 0 });
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	// Fire-and-forget push of the latest rings + quota to the server's ingest. The
	// server diffs and only writes RTDB on change, so pushing on every scan/quota
	// tick is cheap. Null pusher == Switchboard integration disabled.
	void PushSnapshot()
	{
		if (_snapshotPusher is null) return;
		var payload = WidgetSnapshotBuilder.Build(_lastSessions, _lastQuota, DateTimeOffset.Now, _lastTitleStates);
		_snapshotPusher.PushAsync(payload, CancellationToken.None).ContinueWith(t =>
		{
			if (t.IsFaulted) LogError("widget-snapshot-push", t.Exception!);
		}, TaskScheduler.Default);
	}

	// The popup button / tray item posts an action to the server: from a hidden/idle
	// view the button is "Check"; otherwise it is Stop/Clear (both acknowledge).
	void OnClaudeStatusAction()
	{
		string action = _claudeStatusView.Button == ClaudeStatusButton.CheckNow ? "check" : "stop";
		_claudeStatusReader.PostActionAsync(action, CancellationToken.None).ContinueWith(t =>
		{
			if (t.IsFaulted) LogError("claude-status-action", t.Exception!);
			PollClaudeStatus();   // re-sync the dot from the server right after the action
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	// Fire-and-forget GET of the server view; result marshaled back to the UI thread.
	void PollClaudeStatus()
	{
		if (_claudeStatusScanning) return;
		_claudeStatusScanning = true;
		_claudeStatusReader.GetViewAsync(CancellationToken.None).ContinueWith(t =>
		{
			_claudeStatusScanning = false;
			if (t.IsFaulted) { LogError("claude-status-poll", t.Exception!); return; }
			_claudeStatusView = t.Result;
			RefreshClaudeStatusSurfaces();
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	void RefreshClaudeStatusSurfaces()
	{
		var v = _claudeStatusView;
		_panel.UpdateClaudeStatus(v);
		_widget.SetClaudeStatus(v.DotVisible, v.DotLevel);
		_tray.SetClaudeStatusButton(v.Button);
		if (_widget.ClaudePulsing) _claudePulseTimer.Start(); else _claudePulseTimer.Stop();
	}

	// Re-render the countdown at the next moment its text would change (decoupled from the poll), so
	// "3h" -> "2h" -> ... -> "5m" -> "4m" stays live even with a 1-hour poll. Floor 1s, cap 60s.
	void ScheduleCountdown()
	{
		if (_widget.Quota is not QuotaUsage q) { _countdownTimer.Stop(); return; }
		var now = DateTimeOffset.Now;
		var a = QuotaFormat.TimeUntilDisplayChange(q.Session.ResetsAt, now);
		var b = QuotaFormat.TimeUntilDisplayChange(q.Weekly.ResetsAt, now);
		TimeSpan next = MinSpan(a, b) ?? TimeSpan.FromSeconds(60);
		_countdownTimer.Interval = (int)Math.Clamp(next.TotalMilliseconds, 1000, 60_000);
		_countdownTimer.Start();
	}

	static TimeSpan? MinSpan(TimeSpan? a, TimeSpan? b)
		=> a is null ? b : b is null ? a : (a.Value < b.Value ? a : b);

	void SetQuotaEnabled(bool on)
	{
		_config.ShowQuota = on;
		SafeSaveConfig();
		_widget.SetShowQuota(on);
		if (on) { _quotaTimer.Start(); PollQuota(); }
		else { _quotaTimer.Stop(); _countdownTimer.Stop(); }
	}

	void LogInfo(string source, string message)
	{
		try
		{
			Directory.CreateDirectory(Path.GetDirectoryName(_logPath)!);
			File.AppendAllText(_logPath, $"{DateTime.Now:s} [{source}] {message}{Environment.NewLine}");
		}
		catch { /* logging must never crash the widget */ }
	}

	void SafeSaveConfig() { try { _config.Save(); } catch (Exception ex) { LogError("config-save", ex); } }

	// Open the Operator dashboard in the default browser. conversationId, when supplied, deep-links via #conv=.
	void OpenDashboard(string? conversationId = null)
	{
		try
		{
			string url = _config.Switchboard.DashboardUrl;
			if (!string.IsNullOrEmpty(conversationId)) url += "#conv=" + conversationId;
			Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
		}
		catch (Exception ex) { LogError("switchboard-launch", ex); }
	}

	public void Dispose()
	{
		_timer.Dispose();
		_embedWatchdog.Dispose();
		_hoverTimer.Dispose();
		_quotaTimer.Dispose();
		_countdownTimer.Dispose();
		_switchboardTimer.Dispose();
		_claudeStatusTimer.Dispose();
		_claudePulseTimer.Dispose();
		_tray.Dispose();
		_panel.Dispose();
		_widget.Dispose();
	}
}
