using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class DetailPanel : Form
{
	const int WS_EX_TOOLWINDOW = 0x00000080;
	const int WS_EX_NOACTIVATE = 0x08000000;
	const int RowH = 42;
	const int Pad = 12;
	const int QuotaWindowRowH = 46;
	const int GroupVPad = 8;     // inner top/bottom padding inside a group panel
	const int GroupGap = 10;     // vertical gap between the two group panels
	const int PanelMargin = 6;   // horizontal inset of a group panel from the popup edge
	const int PanelRadius = 6;
	const int SwitchboardRowH = 24;
	const int ClaudeStatusRowH = 40;   // status line + incident line

	IReadOnlyList<SessionModel> _sessions = Array.Empty<SessionModel>();
	Palette _palette = new(light: false);
	QuotaUsage? _quota;   // latest Claude plan usage (5h/7d); null until first successful poll -> section hidden
	DateTime? _lastActivityUtc;   // newest transcript mtime when no session is active; null if never seen

	bool _switchboardEnabled;
	SwitchboardStats? _switchboardStats;   // latest /stats; null means unavailable when enabled
	readonly Button _openDashboard;

	ClaudeStatusView? _claudeStatus;
	readonly Button _claudeButton;

	public event Action? OpenDashboardRequested;
	public event Action? ClaudeStatusButtonClicked;

	public DetailPanel()
	{
		FormBorderStyle = FormBorderStyle.None;
		ShowInTaskbar = false;
		TopMost = true;
		StartPosition = FormStartPosition.Manual;
		DoubleBuffered = true;
		Width = 320;
		Visible = false;

		_openDashboard = new Button
		{
			Text = "Open dashboard",
			FlatStyle = FlatStyle.Flat,
			AutoSize = false,
			Height = 26,
			Visible = false,
			TabStop = false,
			Cursor = Cursors.Hand,
			Font = new Font("Segoe UI", 8.5f),
		};
		_openDashboard.FlatAppearance.BorderSize = 0;
		// Soft, rounded button: clip the corners to a rounded region whenever it
		// resizes (its width is set during paint to fit the panel).
		_openDashboard.SizeChanged += (_, _) => ApplyButtonRegion();
		_openDashboard.Click += (_, _) => OpenDashboardRequested?.Invoke();
		Controls.Add(_openDashboard);

		_claudeButton = new Button
		{
			Text = "Check Claude status",
			FlatStyle = FlatStyle.Flat,
			AutoSize = false,
			Height = 26,
			Visible = true,
			TabStop = false,
			Cursor = Cursors.Hand,
			Font = new Font("Segoe UI", 8.5f),
		};
		_claudeButton.FlatAppearance.BorderSize = 0;
		_claudeButton.SizeChanged += (_, _) => ApplyClaudeButtonRegion();
		_claudeButton.Click += (_, _) => ClaudeStatusButtonClicked?.Invoke();
		Controls.Add(_claudeButton);
	}

	void ApplyButtonRegion()
	{
		if (_openDashboard.Width <= 0 || _openDashboard.Height <= 0) return;
		using var path = RoundedRect(_openDashboard.Width, _openDashboard.Height, 7);
		var old = _openDashboard.Region;
		_openDashboard.Region = new Region(path);
		old?.Dispose();
	}

	void ApplyClaudeButtonRegion()
	{
		if (_claudeButton.Width <= 0 || _claudeButton.Height <= 0) return;
		using var path = RoundedRect(_claudeButton.Width, _claudeButton.Height, 7);
		var old = _claudeButton.Region;
		_claudeButton.Region = new Region(path);
		old?.Dispose();
	}

	static GraphicsPath RoundedRect(int w, int h, int r)
	{
		int d = r * 2;
		var path = new GraphicsPath();
		path.AddArc(0, 0, d, d, 180, 90);
		path.AddArc(w - d, 0, d, d, 270, 90);
		path.AddArc(w - d, h - d, d, d, 0, 90);
		path.AddArc(0, h - d, d, d, 90, 90);
		path.CloseFigure();
		return path;
	}

	protected override bool ShowWithoutActivation => true;

	protected override CreateParams CreateParams
	{
		get { var cp = base.CreateParams; cp.ExStyle |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE; return cp; }
	}

	public void UpdateSessions(IReadOnlyList<SessionModel> sessions, bool lightTheme, DateTime? lastActivityUtc)
	{
		_sessions = sessions;
		_palette = new Palette(lightTheme);
		_lastActivityUtc = lastActivityUtc;
		RecomputeHeight();
		Invalidate();
	}

	// Store the latest plan usage; the quota section appears once we have data and grows the panel.
	public void UpdateQuota(QuotaUsage usage)
	{
		_quota = usage;
		RecomputeHeight();
		Invalidate();
	}

	// Latest Switchboard /stats (null == unavailable). enabled gates the whole block; when false it is hidden.
	public void UpdateSwitchboard(bool enabled, SwitchboardStats? stats)
	{
		_switchboardEnabled = enabled;
		_switchboardStats = stats;
		_openDashboard.Visible = enabled;
		RecomputeHeight();
		Invalidate();
	}

	// Latest Claude status-page view (from ClaudeStatusWatch.Snapshot). Always shown: the button is
	// the entry point for a manual check even before any data exists.
	public void UpdateClaudeStatus(ClaudeStatusView view)
	{
		_claudeStatus = view;
		_claudeButton.Text = view.Button switch
		{
			ClaudeStatusButton.StopWatching => "Stop watching",
			ClaudeStatusButton.Clear => "Clear",
			_ => "Check Claude status",
		};
		RecomputeHeight();
		Invalidate();
	}

	void RecomputeHeight()
	{
		int quotaH = _quota.HasValue ? 2 * QuotaWindowRowH + 2 * GroupVPad + GroupGap : 0;
		int ctxH = Math.Max(1, _sessions.Count) * RowH + 2 * GroupVPad;
		int sbH = _switchboardEnabled ? SwitchboardRowH + _openDashboard.Height + 2 * GroupVPad + GroupGap : 0;
		int csH = ClaudeStatusRowH + _claudeButton.Height + 2 * GroupVPad + GroupGap;
		Height = Pad + quotaH + ctxH + sbH + csH + Pad;
	}

	public void ShowAbove(Rectangle widgetScreenBounds)
	{
		int x = Math.Max(0, widgetScreenBounds.Right - Width);
		int y = widgetScreenBounds.Top - Height;   // touch the widget's top edge so hover doesn't break crossing a gap
		Location = new Point(x, y);
		if (!Visible) Show();
		BringToFront();
	}

	static string Human(long n) =>
		n >= 1_000_000 ? $"{n / 1_000_000.0:0.0}M" :
		n >= 1_000 ? $"{n / 1_000}K" : n.ToString();

	protected override void OnPaint(PaintEventArgs e)
	{
		var g = e.Graphics;
		g.SmoothingMode = SmoothingMode.AntiAlias;
		// Opaque panel background, so ClearType sub-pixel text renders crisply (matches the widget).
		g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
		g.Clear(_palette.Background);

		using var label = new Font("Segoe UI", 9f, FontStyle.Bold);
		using var small = new Font("Segoe UI", 7.5f);
		using var textBrush = new SolidBrush(_palette.Text);
		using var mutedBrush = new SolidBrush(_palette.Muted);

		int y = Pad;

		// Group 1: plan-usage windows (5h / 7d) on a filled surface panel.
		if (_quota is QuotaUsage q)
		{
			int groupH = 2 * QuotaWindowRowH + 2 * GroupVPad;
			DrawGroupPanel(g, y, groupH);
			int inner = y + GroupVPad;
			inner = DrawQuotaWindow(g, inner, "5h session", q.Session, QuotaPacing.SessionDuration, label, small);
			DrawQuotaWindow(g, inner, "7d week", q.Weekly, QuotaPacing.WeeklyDuration, label, small);
			y += groupH + GroupGap;
		}

		// Group 2: per-session context usage on its own filled surface panel.
		int ctxTop = y;
		int ctxH = Math.Max(1, _sessions.Count) * RowH + 2 * GroupVPad;
		DrawGroupPanel(g, ctxTop, ctxH);
		if (_sessions.Count == 0)
		{
			// All agents have aged out of the active window; report when the most recent one was last seen.
			string msg = _lastActivityUtc is DateTime last
				? $"last active agent {RelativeTime.Ago(last, DateTime.UtcNow)}"
				: "no recent agent activity";
			var ms = g.MeasureString(msg, small);
			g.DrawString(msg, small, mutedBrush, (Width - ms.Width) / 2, ctxTop + (ctxH - ms.Height) / 2);
		}
		y += GroupVPad;
		foreach (var s in _sessions)   // no-op when empty (message drawn above)
		{
			// status dot
			var dotColor = s.Status == SessionStatus.Live ? Color.FromArgb(63, 185, 80) : _palette.Muted;
			using (var dot = new SolidBrush(dotColor)) g.FillEllipse(dot, Pad, y + 3, 8, 8);

			// line 1: [WSL] label .................... model/window tag
			int labelX = Pad + 16;
			if (s.Distro is not null)
			{
				g.DrawString("WSL", small, mutedBrush, labelX, y + 2);
				labelX += (int)Math.Ceiling(g.MeasureString("WSL", small).Width) + 6;
			}
			g.DrawString(s.Label, label, textBrush, labelX, y);
			var tag = $"{ShortModel(s.Model)} · {WindowTag(s.WindowSize)}";
			var tagSize = g.MeasureString(tag, small);
			g.DrawString(tag, small, mutedBrush, Width - Pad - tagSize.Width, y + 1);

			// line 2: bar + tokens + pct
			int barY = y + 22;
			int barW = Width - Pad * 2 - 120;
			using (var track = new SolidBrush(_palette.Track))
				g.FillRectangle(track, Pad, barY, barW, 8);
			if (!s.IsError)
			{
				using var fill = new SolidBrush(SeverityGradient.For(s.Pct));
				g.FillRectangle(fill, Pad, barY, (int)(barW * Math.Clamp(s.Pct, 0, 1)), 8);
			}
			var tokens = s.IsError ? "?" : $"{Human(s.ContextTokens)} / {Human(s.WindowSize)}";
			g.DrawString(tokens, small, mutedBrush, Pad + barW + 8, barY - 2);
			var pct = s.IsError ? "?" : $"{(int)Math.Round(s.Pct * 100)}%";
			using var pctBrush = new SolidBrush(s.IsError ? _palette.Warning : SeverityGradient.For(s.Pct));
			g.DrawString(pct, label, pctBrush, Width - Pad - 34, barY - 4);

			y += RowH;
		}

		// Group 3: Switchboard readout (gated by config). One line of counts plus a launch button.
		if (_switchboardEnabled)
		{
			int sbTop = ctxTop + ctxH + GroupGap;
			int sbH = SwitchboardRowH + _openDashboard.Height + 2 * GroupVPad;
			DrawGroupPanel(g, sbTop, sbH);
			string sbLine = _switchboardStats is SwitchboardStats st
				? $"Switchboard: {st.ActiveConversations} active - {st.PendingCount} pending - away {(st.AwayMode ? "ON" : "OFF")}"
				: "Switchboard: unavailable";
			g.DrawString(sbLine, label, textBrush, Pad, sbTop + GroupVPad);
			_openDashboard.Location = new Point(Pad, sbTop + GroupVPad + SwitchboardRowH);
			_openDashboard.Width = Width - 2 * Pad;
			// Soft, theme-aware colors so the button blends into the dark panel
			// rather than using the stark default system button face.
			_openDashboard.BackColor = _palette.Track;
			_openDashboard.ForeColor = _palette.Text;
			_openDashboard.FlatAppearance.MouseOverBackColor = ControlPaint.Light(_palette.Track, 0.2f);
			_openDashboard.FlatAppearance.MouseDownBackColor = _palette.Surface;
			y = sbTop + sbH;
		}

		// Group 4: Claude service status (always shown; the button drives the manual check loop).
		{
			// Compute the top from a reliable base: the switchboard block (when drawn) already set y to its
			// absolute bottom; otherwise the context group's bottom is ctxTop + ctxH (y drifts short of it).
			int prevBottom = _switchboardEnabled ? y : ctxTop + ctxH;
			int csTop = prevBottom + GroupGap;
			int csH = ClaudeStatusRowH + _claudeButton.Height + 2 * GroupVPad;
			DrawGroupPanel(g, csTop, csH);
			int inner = csTop + GroupVPad;

			var view = _claudeStatus;
			var level = view?.DotLevel ?? ClaudeStatusLevel.Unknown;
			Color dotColor = view is { HasData: true } || view is { DotVisible: true }
				? Palette.ForClaudeStatus(level)
				: _palette.Muted;
			using (var dot = new SolidBrush(dotColor)) g.FillEllipse(dot, Pad, inner + 3, 8, 8);

			string headline = view is null || !view.HasData
				? "Claude status: not checked"
				: $"Claude: {(view.Description.Length > 0 ? view.Description : level.ToString())}";
			g.DrawString(Ellipsize(g, headline, label, Width - (Pad + 16) - Pad), label, textBrush, Pad + 16, inner);

			// "checked N ago" sits on line 2 (right) so it never collides with a long headline.
			string ago = view is { FetchedAtUtc: DateTime fetched }
				? "checked " + RelativeTime.Ago(fetched, DateTime.UtcNow)
				: "";
			float agoW = ago.Length > 0 ? g.MeasureString(ago, small).Width : 0f;
			if (ago.Length > 0)
				g.DrawString(ago, small, mutedBrush, Width - Pad - agoW, inner + 18);

			string second = view is { IncidentNames.Count: > 0 }
				? string.Join("; ", view.IncidentNames)
				: "";
			if (second.Length > 0)
			{
				float avail = Width - (Pad + 16) - agoW - 8f;
				g.DrawString(Ellipsize(g, second, small, avail), small, mutedBrush, Pad + 16, inner + 18);
			}

			_claudeButton.Location = new Point(Pad, csTop + GroupVPad + ClaudeStatusRowH);
			_claudeButton.Width = Width - 2 * Pad;
			_claudeButton.BackColor = _palette.Track;
			_claudeButton.ForeColor = _palette.Text;
			_claudeButton.FlatAppearance.MouseOverBackColor = ControlPaint.Light(_palette.Track, 0.2f);
			_claudeButton.FlatAppearance.MouseDownBackColor = _palette.Surface;
		}
	}

	int DrawQuotaWindow(Graphics g, int y, string name, QuotaWindow w, TimeSpan duration, Font label, Font small)
	{
		var now = DateTimeOffset.Now;
		var pace = QuotaPacing.Compute(w, duration, now);
		double usage01 = Math.Clamp(w.Percentage / 100.0, 0, 1);
		var color = SeverityGradient.For(usage01);

		using var textBrush = new SolidBrush(_palette.Text);
		using var mutedBrush = new SolidBrush(_palette.Muted);

		// line 1: window name (left) + exact reset time (right)
		g.DrawString(name, label, textBrush, Pad, y);
		string reset = QuotaFormat.FormatResetTime(w.ResetsAt, now);
		if (reset.Length > 0)
		{
			string resetText = "resets " + reset;
			var rs = g.MeasureString(resetText, small);
			g.DrawString(resetText, small, mutedBrush, Width - Pad - rs.Width, y + 2);
		}

		// line 2: continuous usage bar (severity gradient), matching the popup's session-row bars
		int barY = y + 18;
		int barW = Width - Pad * 2;
		using (var track = new SolidBrush(_palette.Track))
			g.FillRectangle(track, Pad, barY, barW, 8);
		using (var fill = new SolidBrush(color))
			g.FillRectangle(fill, Pad, barY, (int)(barW * usage01), 8);

		// ghost pace bar beneath: fill to the elapsed-time fraction, amber when burning over pace
		// (matching the caption tint), muted otherwise. Omitted when reset unknown.
		int ghostY = barY + 10;
		if (pace.ElapsedFraction is double ef)
		{
			using var ghostTrack = new SolidBrush(_palette.Track);
			g.FillRectangle(ghostTrack, Pad, ghostY, barW, 3);
			var ghostColor = pace.Verdict == PaceVerdict.Over ? _palette.Warning : _palette.Muted;
			using var ghostFill = new SolidBrush(ghostColor);
			g.FillRectangle(ghostFill, Pad, ghostY, (int)(barW * ef), 3);
		}

		// caption: "NN% used · time elapsed NN%" - the elapsed part tints amber when burning ahead of pace
		int capY = ghostY + 6;
		string usedText = $"{(int)Math.Round(w.Percentage)}% used";
		using (var ub = new SolidBrush(color))
			g.DrawString(usedText, small, ub, Pad, capY);
		if (pace.ElapsedFraction is double ef2)
		{
			float usedW = g.MeasureString(usedText, small).Width;
			const string sep = " · ";
			float sepW = g.MeasureString(sep, small).Width;
			g.DrawString(sep, small, mutedBrush, Pad + usedW, capY);
			string elapsedText = $"time elapsed {(int)Math.Round(ef2 * 100)}%";
			var paceColor = pace.Verdict == PaceVerdict.Over ? _palette.Warning : _palette.Muted;
			using var pb = new SolidBrush(paceColor);
			g.DrawString(elapsedText, small, pb, Pad + usedW + sepW, capY);
		}

		return y + QuotaWindowRowH;
	}

	// Filled rounded surface panel behind a group of rows (the "group box" treatment).
	void DrawGroupPanel(Graphics g, int top, int height)
	{
		using var b = new SolidBrush(_palette.Surface);
		FillRoundedRect(g, b, new Rectangle(PanelMargin, top, Width - 2 * PanelMargin, height), PanelRadius);
	}

	static void FillRoundedRect(Graphics g, Brush brush, Rectangle r, int radius)
	{
		int d = radius * 2;
		using var path = new GraphicsPath();
		path.AddArc(r.X, r.Y, d, d, 180, 90);
		path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
		path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
		path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
		path.CloseFigure();
		g.FillPath(brush, path);
	}

	// Trim text to fit maxWidth, appending an ellipsis. Returns the input unchanged when it already fits.
	static string Ellipsize(Graphics g, string text, Font font, float maxWidth)
	{
		if (maxWidth <= 0 || g.MeasureString(text, font).Width <= maxWidth) return text;
		const string ell = "...";
		for (int len = text.Length - 1; len > 0; len--)
		{
			string candidate = text.Substring(0, len) + ell;
			if (g.MeasureString(candidate, font).Width <= maxWidth) return candidate;
		}
		return ell;
	}

	static string ShortModel(string? model)
	{
		if (string.IsNullOrEmpty(model)) return "model?";
		if (model.Contains("opus", StringComparison.OrdinalIgnoreCase)) return "Opus";
		if (model.Contains("sonnet", StringComparison.OrdinalIgnoreCase)) return "Sonnet";
		if (model.Contains("haiku", StringComparison.OrdinalIgnoreCase)) return "Haiku";
		return model;
	}

	static string WindowTag(long window) => window >= 1_000_000 ? "1M" : $"{window / 1000}K";
}
