using System.Diagnostics;
using System.Globalization;
using System.IO;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class AppHost : IDisposable
{
	readonly AppConfig _config;
	readonly WidgetWindow _widget = new();
	readonly DetailPanel _panel = new();
	readonly TrayIcon _tray;
	readonly System.Windows.Forms.Timer _embedWatchdog = new();
	readonly System.Windows.Forms.Timer _hoverTimer = new();
	readonly System.Windows.Forms.Timer _countdownTimer = new();  // self-rescheduling countdown ticker
	readonly DailyAnchor _anchor;
	readonly string _logPath;
	// The server's GET /stats is the only data source: live sessions, plan usage, pending and away state.
	readonly System.Windows.Forms.Timer _switchboardTimer = new();
	readonly SwitchboardStatsReader _switchboardReader;
	volatile bool _switchboardScanning;
	readonly SessionsView _sessionsView = new();      // the newest poll, and what the widget, popup and tray last drew
	QuotaUsage? _lastQuota;
	SwitchboardStats? _lastSwitchboardStats;         // last successful /stats read (survives unavailable ticks)
	DateTime _lastSavedUtc = DateTime.MinValue;       // the render cache is written at most once a minute
	readonly System.Windows.Forms.Timer _claudeStatusTimer = new();   // steady GET-poll of the server view
	readonly ClaudeStatusReader _claudeStatusReader;
	volatile bool _claudeStatusScanning;
	ClaudeStatusView _claudeStatusView;                               // latest server view (drives the surfaces)
	readonly System.Windows.Forms.Timer _claudePulseTimer = new();
	readonly System.Windows.Forms.Timer _anchorTimer = new();   // once-a-day session-window anchor
	DateOnly? _anchorHandledDate;                                // in-memory; the day the anchor was last decided
	volatile bool _anchorRunning;

	readonly bool _isDemo;

	public AppHost(AppConfig config, bool isDemo = false)
	{
		_config = config;
		_isDemo = isDemo;
		_logPath = Path.Combine(Path.GetDirectoryName(AppConfig.DefaultPath)!, "log.txt");
		_anchor = new DailyAnchor(m => LogInfo("anchor", m), LogError);
		_tray = new TrayIcon(_config.Autostart, _config.ShowQuota);

		// Polled whether or not the Switchboard UI is enabled: it is where sessions and quota come from.
		_switchboardReader = new SwitchboardStatsReader(_config.Switchboard.StatsUrl, LogError);
		_switchboardTimer.Interval = Math.Max(2, _config.Switchboard.PollSeconds) * 1000;
		_switchboardTimer.Tick += (_, _) => PollSwitchboard();

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
		_panel.SetAwayModeOnRequested += () =>
		{
			_ = Task.Run(async () =>
			{
				await _switchboardReader.SetAwayModeOnAsync(CancellationToken.None).ConfigureAwait(false);
				_widget.Invoke(PollSwitchboard);
			});
		};

		_widget.PreferredX = _config.WidgetX;
		_widget.PositionChanged += x => { _config.WidgetX = x; SafeSaveConfig(); };
		_widget.Diagnostic = m => LogInfo("embed", m);

		_tray.RefreshRequested += PollSwitchboard;
		_tray.RenderModeToggled += clearType => _widget.SetRenderMode(clearType);
		_tray.QuotaShowToggled += SetQuotaEnabled;
		_tray.AutostartToggled += on =>
		{
			_config.Autostart = on;
			Autostart.Apply(on, Application.ExecutablePath);
			SafeSaveConfig();
		};
		_tray.WakeTimeRequested += ConfigureWakeTime;
		_tray.SetWakeTime(_config.DailyAnchorEnabled, _config.DailyAnchorTimeOfDay);
		_tray.QuitRequested += () => Application.Exit();

		_embedWatchdog.Interval = 1000;
		_embedWatchdog.Tick += (_, _) => _widget.KeepOnTop();

		_hoverTimer.Interval = 200;
		_hoverTimer.Tick += (_, _) => UpdateHover();

		_countdownTimer.Tick += (_, _) => { _widget.RefreshQuotaCountdown(); ScheduleCountdown(); };

		_anchorTimer.Interval = 60_000;
		_anchorTimer.Tick += (_, _) => MaybeFireDailyAnchor();
	}

	public void Start()
	{
		Autostart.Apply(_config.Autostart, Application.ExecutablePath);
		_widget.Show();
		_widget.AttachToTaskbar();
		_embedWatchdog.Start();
		_hoverTimer.Start();
		_widget.SetShowQuota(_config.ShowQuota);

		if (_isDemo)
		{
			ApplyDemoData();
			_switchboardTimer.Start();
			PollSwitchboard();
			return;
		}

		RenderLastKnown(LastKnownStore.LoadFrom(LastKnownStore.DefaultPath));
		_switchboardTimer.Start();
		PollSwitchboard();
		_claudeStatusTimer.Start();
		PollClaudeStatus();
		_anchorTimer.Start();
	}

	void ApplyDemoData()
	{
		bool light = _config.LightThemeOverride ?? ThemeReader.IsLightTaskbar();
		var now = DateTime.UtcNow;

		var sessions = new List<SessionModel>
		{
			new("Switchboard", null, 112000, 200000, "claude-3-7-fable", SessionStatus.Live, now, false, "demo-1", "Switchboard", "project"),
			new("Switchboard", "Ubuntu", 94000, 200000, "claude-3-5-sonnet", SessionStatus.Live, now, false, "demo-2", "Switchboard", "project"),
			new("Switchboard", null, 380000, 1000000, "claude-sonnet-4-5", SessionStatus.Live, now, false, "demo-3", "Switchboard", "project"),
			new("Switchboard", null, 44000, 200000, "claude-3-opus", SessionStatus.Live, now, false, "demo-4", "Switchboard", "project"),
			new("Switchboard", null, 24000, 200000, "claude-3-5-haiku", SessionStatus.Idle, now, false, "demo-5", "Switchboard", "project"),
		};

		var quota = new QuotaUsage(
			Session: new QuotaWindow(84.0, DateTimeOffset.Now.AddHours(1).AddMinutes(30)),
			Weekly: new QuotaWindow(85.0, DateTimeOffset.Now.AddHours(15).AddMinutes(7))
		);

		var needsYou = new Dictionary<string, NeedsYouEntry>
		{
			["demo-1"] = new("AskUserQuestion pending", 45.0)
		};

		bool awayMode = _lastSwitchboardStats?.AwayMode ?? false;

		var stats = new SwitchboardStats(
			ActiveConversations: 2,
			PendingCount: 1,
			OldestPendingAgeSeconds: 45.0,
			AwayMode: awayMode,
			Healthy: true
		) { NeedsYou = needsYou };

		_sessionsView.Update(sessions, light);
		_lastQuota = quota;

		_widget.UpdateQuota(quota);
		_panel.UpdateQuota(quota);

		_widget.UpdateSessions(sessions, light);
		_panel.UpdateSessions(sessions, light);

		_panel.UpdateSwitchboard(enabled: true, stats);
		_panel.UpdateClaudeStatus(ClaudeServerStatus.ParseView("""
		{
			"dot_visible": true,
			"has_data": true,
			"level": "major",
			"description": "Major Outage",
			"incidents": ["Elevated API Errors"],
			"button": "stop"
		}
		"""));
		_tray.SetPending(showBadge: true, hasPending: true);
		_widget.SetPending(showBadge: true, hasPending: true);
		_widget.SetAwayMode(awayMode);

		var gauge = TrayGauge.From(sessions);
		_tray.SetGauge(gauge.Max, gauge.AnyError, gauge.MaxSeverity, light);
	}

	DateTime? _hoverStartTime;

	// Tooltip-style: show the panel while the cursor is over the widget (or the panel itself), hide otherwise.
	void UpdateHover()
	{
		var p = Cursor.Position;
		bool overWidget = _widget.ScreenBounds.Contains(p);
		bool over = overWidget || (_panel.Visible && _panel.Bounds.Contains(p));
		_widget.SetHovered(over);
		if (over)
		{
			if (!_panel.Visible)
			{
				_hoverStartTime ??= DateTime.UtcNow;
				if (DateTime.UtcNow - _hoverStartTime.Value >= TimeSpan.FromMilliseconds(750))
				{
					_panel.ShowAbove(_widget.ScreenBounds);
				}
			}
		}
		else
		{
			_hoverStartTime = null;
			if (_panel.Visible)
			{
				_panel.Hide();
			}
		}
	}

	void LogError(string source, Exception ex) => WatchtowerLog.Error(source, ex, _logPath);

	// Once-a-day anchor: on the first minute-tick inside [anchorTime, anchorTime + grace) that has not
	// been handled today, decide on a background thread whether to skip (a window is already open) or
	// fire the headless anchor turn. Awake-only and no catch-up fall out of the narrow gate window.
	void MaybeFireDailyAnchor()
	{
		if (!_config.DailyAnchorEnabled || _anchorRunning) return;
		var now = DateTimeOffset.Now;
		if (!DailyAnchorSchedule.ShouldEvaluate(now.LocalDateTime, _config.DailyAnchorTimeOfDay, DailyAnchorSchedule.Grace, _anchorHandledDate))
			return;

		_anchorRunning = true;
		// The latest live /stats quota (never the startup cache, which carries none): null fires.
		var serverQuota = _lastSwitchboardStats?.Quota;
		Task.Run(() => _anchor.TryRun(now, serverQuota)).ContinueWith(t =>
		{
			_anchorRunning = false;
			if (t.IsFaulted) { LogError("anchor", t.Exception!); return; }
			if (t.Result != AnchorOutcome.Failed)
				_anchorHandledDate = DateOnly.FromDateTime(now.LocalDateTime);
			LogInfo("anchor", $"daily session anchor: {t.Result}");
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	// Fire-and-forget /stats poll; result marshaled back to the UI thread. Null == unavailable: the last
	// view stays up and the panel's Switchboard pill reads unavailable.
	void PollSwitchboard()
	{
		if (_switchboardScanning) return;
		_switchboardScanning = true;
		_switchboardReader.FetchAsync(CancellationToken.None).ContinueWith(t =>
		{
			_switchboardScanning = false;
			if (t.IsFaulted) { LogError("switchboard-poll", t.Exception!); return; }
			var stats = t.Result;
			if (_isDemo)
			{
				if (stats is not null) _lastSwitchboardStats = stats;
				ApplyDemoData();
				return;
			}
			bool enabled = _config.Switchboard.Enabled;
			_panel.UpdateSwitchboard(enabled, stats);
			bool showBadge = enabled && _config.Switchboard.ShowBadge;
			_tray.SetPending(showBadge, stats is { PendingCount: > 0 });
			_widget.SetPending(showBadge, stats is { PendingCount: > 0 });
			_widget.SetAwayMode(enabled && stats is { AwayMode: true });
			if (stats is null) return;
			_lastSwitchboardStats = stats;
			ApplySessions(StatsSessions.ToModels(stats.Sessions, DateTime.UtcNow));
			if (_config.ShowQuota && stats.Quota is QuotaUsage q) ApplyQuota(q, stats.QuotaReceivedAt);
			SaveLastKnown();
		}, TaskScheduler.FromCurrentSynchronizationContext());
	}

	void ApplySessions(IReadOnlyList<SessionModel> sessions)
	{
		bool light = _config.LightThemeOverride ?? ThemeReader.IsLightTaskbar();
		// The /stats poll runs every few seconds; lay out only when the view changes.
		if (!_sessionsView.Update(sessions, light)) return;
		_widget.UpdateSessions(sessions, light);
		_panel.UpdateSessions(sessions, light);
		// Tray ring gauge mirrors the busiest session (same rule the widget's % label uses).
		var gauge = TrayGauge.From(sessions);
		_tray.SetGauge(gauge.Max, gauge.AnyError, gauge.MaxSeverity, light);
	}

	void ApplyQuota(QuotaUsage quota, DateTimeOffset? receivedAt)
	{
		_panel.SetQuotaReceivedAt(receivedAt);
		if (_lastQuota == quota) return;
		_lastQuota = quota;
		_widget.UpdateQuota(quota);
		_panel.UpdateQuota(quota);
		ScheduleCountdown();
	}

	// Persist the render cache at most once a minute: the /stats poll runs every few seconds.
	void SaveLastKnown()
	{
		var now = DateTime.UtcNow;
		if (now - _lastSavedUtc < TimeSpan.FromMinutes(1)) return;
		_lastSavedUtc = now;
		var state = LastKnownStore.From(_sessionsView.Latest, _lastQuota, _lastSwitchboardStats, now);
		if (!LastKnownStore.SaveTo(state, LastKnownStore.DefaultPath))
			LogInfo("last-known", "save failed; startup render will use the previous cache");
	}

	// Render the cached last-known state before the first /stats poll lands, so startup is not blank.
	// Stale-session guard: session bars older than the freshness window are skipped (they would
	// advertise live agents that are long gone); quota and stats render at any age and are replaced
	// by the first live poll within seconds.
	void RenderLastKnown(LastKnownState? state)
	{
		bool enabled = _config.Switchboard.Enabled;
		var cachedStats = state is not null ? LastKnownStore.ToStats(state) : null;
		if (cachedStats is not null) _lastSwitchboardStats = cachedStats;
		_panel.UpdateSwitchboard(enabled, cachedStats);
		if (state is null) return;

		bool light = _config.LightThemeOverride ?? ThemeReader.IsLightTaskbar();
		var sessions = LastKnownStore.RenderableSessions(state, DateTime.UtcNow);
		if (sessions.Count > 0) ApplySessions(sessions);
		if (_config.ShowQuota && LastKnownStore.ToQuota(state) is QuotaUsage q)
		{
			_lastQuota = q;
			_widget.UpdateQuota(q);
			_panel.UpdateQuota(q);
			ScheduleCountdown();
		}
		if (cachedStats is not null)
		{
			bool showBadge = enabled && _config.Switchboard.ShowBadge;
			bool hasPending = cachedStats is { PendingCount: > 0 };
			_tray.SetPending(showBadge, hasPending);
			_widget.SetPending(showBadge, hasPending);
			_widget.SetAwayMode(enabled && cachedStats is { AwayMode: true });
		}
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
		if (_isDemo || _claudeStatusScanning) return;
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
		if (on) PollSwitchboard();
		else _countdownTimer.Stop();
	}

	void LogInfo(string source, string message) => WatchtowerLog.Info(source, message, _logPath);

	void SafeSaveConfig()
	{
		try { if (!_config.Save()) LogInfo("config-save", "skipped: config load was degraded; not overwriting"); }
		catch (Exception ex) { LogError("config-save", ex); }
	}

	void ConfigureWakeTime()
	{
		using var dlg = new WakeTimeDialog(_config.DailyAnchorEnabled, _config.DailyAnchorTimeOfDay);
		if (dlg.ShowDialog() == DialogResult.OK)
		{
			_config.DailyAnchorEnabled = dlg.WakeTimeEnabled;
			_config.DailyAnchorTime = dlg.WakeTime.ToString("HH:mm", CultureInfo.InvariantCulture);
			SafeSaveConfig();
			_tray.SetWakeTime(_config.DailyAnchorEnabled, _config.DailyAnchorTimeOfDay);
			LogInfo("wake-time", _config.DailyAnchorEnabled
				? $"wake time updated to {_config.DailyAnchorTime}"
				: "wake feature disabled");
		}
	}

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
		_embedWatchdog.Dispose();
		_hoverTimer.Dispose();
		_countdownTimer.Dispose();
		_switchboardTimer.Dispose();
		_claudeStatusTimer.Dispose();
		_claudePulseTimer.Dispose();
		_anchorTimer.Dispose();
		_tray.Dispose();
		_panel.Dispose();
		_widget.Dispose();
	}
}
