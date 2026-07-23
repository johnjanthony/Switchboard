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
	const int QuotaPausedRowH = 18;
	const int GroupVPad = 8;     // inner top/bottom padding inside a group panel
	const int GroupGap = 10;     // vertical gap between the two group panels
	const int PanelMargin = 6;   // horizontal inset of a group panel from the popup edge
	const int PanelRadius = 6;
	const int BottomPillRowH = 26;

	// Backdrop color, keyed to full transparency (Form.TransparencyKey). The card fills are anti-aliased
	// and a color key only removes exact-key pixels, so the blended edge pixels survive as a fringe. This
	// value tunes how that fringe reads: a pure grey partway between the card surface (#2A2A2A) and black,
	// so the AA edge fades card-grey -> this grey as a soft rim with enough range to keep the corners
	// smooth. Lighter (toward surface) = subtler rim but harder corners; darker (toward black) = smoother
	// gradient but heavier rim. It is a pure grey (R=G=B): the only anti-aliased edge that lands exactly
	// on it is the card fading into the backdrop, so no other element (pills, borders, dots - all off the
	// grey axis or lighter) gets a pixel keyed out by accident.
	static readonly Color BackdropKey = Color.FromArgb(20, 20, 20);

	IReadOnlyList<SessionModel> _sessions = Array.Empty<SessionModel>();
	Palette _palette = new(light: false);
	QuotaUsage? _quota;   // latest Claude plan usage (5h/7d); null until first successful poll -> section hidden
	bool _quotaAuthPaused;
	DateTime? _lastActivityUtc;   // newest transcript mtime when no session is active; null if never seen

	bool _switchboardEnabled;
	SwitchboardStats? _switchboardStats;   // latest /stats; null means unavailable when enabled
	IReadOnlyDictionary<string, NeedsYouEntry> _needsYou = SwitchboardStats.EmptyNeedsYou;   // last-good needs-you map; held across unavailable polls
	readonly PillButton _switchboardPillButton;

	ClaudeStatusView? _claudeStatus;
	readonly PillButton _claudePillButton;
	readonly PillButton _awayPillButton;
	readonly ToolTip _toolTip;

	public event Action? OpenDashboardRequested;
	public event Action? ClaudeStatusButtonClicked;
	public event Action? SetAwayModeOnRequested;

	public DetailPanel()
	{
		FormBorderStyle = FormBorderStyle.None;
		ShowInTaskbar = false;
		TopMost = true;
		StartPosition = FormStartPosition.Manual;
		DoubleBuffered = true;
		// Color-key the non-card backdrop to transparency so the rounded cards float over the desktop.
		BackColor = BackdropKey;
		TransparencyKey = BackdropKey;
		Width = 320;
		Visible = false;

		_toolTip = new ToolTip();

		_claudePillButton = new PillButton
		{
			Text = "CLAUDE",
			Visible = true,
		};
		_claudePillButton.Click += (_, _) => OnClaudePillClicked();
		Controls.Add(_claudePillButton);

		_switchboardPillButton = new PillButton
		{
			Text = "SWITCHBOARD",
			Visible = true,
		};
		_switchboardPillButton.Click += (_, _) => OpenDashboardRequested?.Invoke();
		Controls.Add(_switchboardPillButton);

		_awayPillButton = new PillButton
		{
			Text = "AWAY",
			HasMoonIcon = true,
			Visible = true,
		};
		_awayPillButton.Click += (_, _) => OnAwayPillClicked();
		Controls.Add(_awayPillButton);
	}

	void OnAwayPillClicked()
	{
		bool awayOn = _switchboardStats is { AwayMode: true };
		if (awayOn)
		{
			OpenDashboardRequested?.Invoke();
		}
		else
		{
			SetAwayModeOnRequested?.Invoke();
		}
	}

	void OnClaudePillClicked()
	{
		var level = _claudeStatus?.DotLevel ?? ClaudeStatusLevel.Unknown;
		bool isGreen = _claudeStatus is null || level == ClaudeStatusLevel.Operational || level == ClaudeStatusLevel.Unknown;
		if (isGreen)
		{
			ClaudeStatusButtonClicked?.Invoke();
		}
		else
		{
			try
			{
				System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("https://status.claude.com") { UseShellExecute = true });
			}
			catch { }
		}
	}

	static GraphicsPath RoundedRectPath(RectangleF r, int radius)
	{
		int d = radius * 2;
		var path = new GraphicsPath();
		path.AddArc(r.X, r.Y, d, d, 180, 90);
		path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
		path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
		path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
		path.CloseFigure();
		return path;
	}

	static GraphicsPath RoundedRect(int w, int h, int r) => RoundedRectPath(new RectangleF(0, 0, w, h), r);

	static void FillRoundedRect(Graphics g, Brush brush, Rectangle r, int radius)
	{
		using var path = RoundedRectPath(r, radius);
		g.FillPath(brush, path);
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
		_claudePillButton.SurfaceColor = _palette.Surface;
		_switchboardPillButton.SurfaceColor = _palette.Surface;
		_awayPillButton.SurfaceColor = _palette.Surface;
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

	// Auth-failure banner for the quota section: shown while the quota poll is backed off.
	public void SetQuotaAuthPaused(bool paused)
	{
		if (_quotaAuthPaused == paused) return;
		_quotaAuthPaused = paused;
		RecomputeHeight();
		Invalidate();
	}

	// Latest Switchboard /stats (null == unavailable).
	public void UpdateSwitchboard(bool enabled, SwitchboardStats? stats)
	{
		_switchboardEnabled = enabled;
		_switchboardStats = stats;
		if (stats is not null) _needsYou = stats.NeedsYou;   // null poll = unreachable: keep the dots, the stats line shows the outage

		bool reachable = enabled && stats is not null;
		bool healthy = stats is { Healthy: true };

		Color dotColor = !reachable
			? StatusColors.Red
			: (healthy ? StatusColors.Green : StatusColors.Yellow);

		Color textColor = !reachable
			? Color.White
			: (healthy ? Color.FromArgb(99, 109, 125) : Color.FromArgb(205, 212, 221));

		_switchboardPillButton.DotColor = dotColor;
		_switchboardPillButton.ForeColor = textColor;
		_switchboardPillButton.SurfaceColor = _palette.Surface;
		_switchboardPillButton.BackColor = Color.FromArgb(8, 9, 11);
		_switchboardPillButton.BorderColor = _palette.Track;

		string sbTitle = !reachable
			? "Switchboard server unreachable"
			: (healthy ? "Switchboard server healthy" : "Switchboard server degraded");
		_toolTip.SetToolTip(_switchboardPillButton, sbTitle);

		bool awayOn = stats is { AwayMode: true };
		Color awayTextColor = awayOn ? StatusColors.Amber : Color.FromArgb(100, 110, 125);
		Color awayBgColor = awayOn ? Color.FromArgb(52, 37, 16) : Color.FromArgb(8, 9, 11);
		Color awayBorderColor = awayOn ? Color.FromArgb(145, 96, 15) : _palette.Track;

		_awayPillButton.MoonIconColor = awayTextColor;
		_awayPillButton.ForeColor = awayTextColor;
		_awayPillButton.SurfaceColor = _palette.Surface;
		_awayPillButton.BackColor = awayBgColor;
		_awayPillButton.BorderColor = awayBorderColor;

		string awayTooltip = awayOn
			? "Away mode active - open Operator dashboard"
			: "Turn on global away mode";
		_toolTip.SetToolTip(_awayPillButton, awayTooltip);

		RecomputeHeight();
		Invalidate();
	}

	// Latest Claude status view (published by the server, parsed via ClaudeServerStatus.ParseView).
	public void UpdateClaudeStatus(ClaudeStatusView view)
	{
		_claudeStatus = view;
		var level = view?.DotLevel ?? ClaudeStatusLevel.Unknown;
		bool isWatching = view is not null && view.Button != ClaudeStatusButton.CheckNow;
		bool isGreen = view is null || level == ClaudeStatusLevel.Operational || level == ClaudeStatusLevel.Unknown;

		Color dotColor = isGreen
			? (view is { HasData: true } || view is { DotVisible: true } ? StatusColors.Green : _palette.Muted)
			: Palette.ForClaudeStatus(level);

		Color textColor = !isGreen
			? (level == ClaudeStatusLevel.Minor ? Color.FromArgb(205, 212, 221) : Color.White)
			: Color.FromArgb(99, 109, 125);

		_claudePillButton.DotColor = dotColor;
		_claudePillButton.ForeColor = textColor;
		_claudePillButton.SurfaceColor = _palette.Surface;
		_claudePillButton.BackColor = Color.FromArgb(8, 9, 11);
		_claudePillButton.BorderColor = _palette.Track;

		string claudeTitle = view is null || !view.HasData
			? "Claude status"
			: (!string.IsNullOrEmpty(view.Description) ? view.Description : "Claude status");

		if (view is { IncidentNames.Count: > 0 })
		{
			string incidentsText = string.Join("; ", view.IncidentNames);
			if (string.IsNullOrEmpty(claudeTitle) || claudeTitle.Contains("all systems operational", StringComparison.OrdinalIgnoreCase))
			{
				claudeTitle = incidentsText;
			}
			else if (!claudeTitle.Contains(incidentsText, StringComparison.OrdinalIgnoreCase))
			{
				claudeTitle = claudeTitle + " - " + incidentsText;
			}
		}
		_toolTip.SetToolTip(_claudePillButton, claudeTitle);

		RecomputeHeight();
		Invalidate();
	}

	void RecomputeHeight()
	{
		int quotaContentH = (_quota.HasValue ? 2 * QuotaWindowRowH : 0) + (_quotaAuthPaused ? QuotaPausedRowH : 0);
		int quotaH = quotaContentH > 0 ? quotaContentH + 2 * GroupVPad + GroupGap : 0;
		int ctxH = Math.Max(1, _sessions.Count) * RowH + 2 * GroupVPad;
		int group3H = BottomPillRowH + 2 * GroupVPad + GroupGap;
		int group4H = BottomPillRowH + 2 * GroupVPad + GroupGap;
		Height = Pad + quotaH + ctxH + group3H + group4H + Pad;
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
		// Fill the backdrop with the transparency key so the desktop shows through the margins and
		// inter-card gaps. Every string is drawn on an opaque group panel below, not on this backdrop,
		// so ClearType sub-pixel text still renders crisply against a solid surface.
		g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
		g.Clear(BackdropKey);

		using var label = new Font("Segoe UI", 9f, FontStyle.Bold);
		using var small = new Font("Segoe UI", 7.5f);
		using var textBrush = new SolidBrush(_palette.Text);
		using var mutedBrush = new SolidBrush(_palette.Muted);

		int y = Pad;

		// Group 1: plan-usage windows (5h / 7d), plus the auth-paused banner when polling is backed off.
		if (_quota.HasValue || _quotaAuthPaused)
		{
			int groupH = (_quota.HasValue ? 2 * QuotaWindowRowH : 0) + (_quotaAuthPaused ? QuotaPausedRowH : 0) + 2 * GroupVPad;
			DrawGroupPanel(g, y, groupH);
			int inner = y + GroupVPad;
			if (_quotaAuthPaused)
			{
				using var warn = new SolidBrush(_palette.Warning);
				g.DrawString("quota paused - Claude login required", small, warn, Pad, inner);
				inner += QuotaPausedRowH;
			}
			if (_quota is QuotaUsage q)
			{
				inner = DrawQuotaWindow(g, inner, "5h session", q.Session, QuotaPacing.SessionDuration, label, small);
				DrawQuotaWindow(g, inner, "7d week", q.Weekly, QuotaPacing.WeeklyDuration, label, small);
			}
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
		foreach (var s in OrderedSessions())   // no-op when empty (message drawn above)
		{
			// status dot: needs-you (amber) outranks liveness
			bool needsYou = s.SessionId is not null && _needsYou.ContainsKey(s.SessionId);
			var dotColor = needsYou ? StatusColors.Amber : (s.Status == SessionStatus.Live ? StatusColors.Green : _palette.Muted);
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

		// Group 3: Group panel containing Claude & Switchboard indicator pills.
		int group3Top = ctxTop + ctxH + GroupGap;
		int group3H = BottomPillRowH + 2 * GroupVPad;
		DrawGroupPanel(g, group3Top, group3H);

		int btn3Y = group3Top + GroupVPad;
		int padX = 14;
		int btnGap = 8;
		int availableW = Width - 2 * padX - btnGap;
		int btnW = availableW / 2;

		_claudePillButton.Location = new Point(padX, btn3Y);
		_claudePillButton.Size = new Size(btnW, BottomPillRowH);

		_switchboardPillButton.Location = new Point(padX + btnW + btnGap, btn3Y);
		_switchboardPillButton.Size = new Size(btnW, BottomPillRowH);

		// Group 4: Single group panel containing the Away Mode pill button.
		int group4Top = group3Top + group3H + GroupGap;
		int group4H = BottomPillRowH + 2 * GroupVPad;
		DrawGroupPanel(g, group4Top, group4H);

		int btn4Y = group4Top + GroupVPad;
		int fullW = Width - 2 * padX;

		_awayPillButton.Location = new Point(padX, btn4Y);
		_awayPillButton.Size = new Size(fullW, BottomPillRowH);
	}

	private sealed class PillButton : Button
	{
		[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
		public Color DotColor { get; set; } = Color.Gray;

		[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
		public Color BorderColor { get; set; } = Color.FromArgb(30, 41, 59);

		[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
		public Color SurfaceColor { get; set; } = Color.FromArgb(42, 42, 42);

		[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
		public bool HasMoonIcon { get; set; }

		[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
		public Color MoonIconColor { get; set; } = Color.Gray;

		public PillButton()
		{
			FlatStyle = FlatStyle.Flat;
			FlatAppearance.BorderSize = 0;
			AutoSize = false;
			TabStop = false;
			Cursor = Cursors.Hand;
			Font = new Font("Segoe UI", 8f, FontStyle.Bold);
			SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
		}

		protected override void OnPaint(PaintEventArgs e)
		{
			var g = e.Graphics;
			g.SmoothingMode = SmoothingMode.AntiAlias;
			g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;

			g.Clear(SurfaceColor);

			int r = Height / 2;
			using (var path = RoundedRectPath(new RectangleF(0, 0, Width, Height), r))
			using (var bgBrush = new SolidBrush(BackColor))
			{
				g.FillPath(bgBrush, path);
			}

			using (var borderPen = new Pen(BorderColor, 1f))
			using (var borderPath = RoundedRectPath(new RectangleF(0.5f, 0.5f, Width - 1f, Height - 1f), r))
			{
				g.DrawPath(borderPen, borderPath);
			}

			float textW = g.MeasureString(Text, Font, PointF.Empty, StringFormat.GenericTypographic).Width;
			float iconW = HasMoonIcon ? 13f : 6f;
			float iconGap = 6f;
			float contentW = iconW + iconGap + textW;
			float startX = (Width - contentW) / 2f;

			if (HasMoonIcon)
			{
				float moonSize = 13f;
				float moonY = (Height - moonSize) / 2f;
				using var moonPath = CreateMoonPath(startX, moonY, moonSize);
				using var moonBrush = new SolidBrush(MoonIconColor);
				g.FillPath(moonBrush, moonPath);
			}
			else
			{
				float dotDiameter = 6f;
				float dotY = (Height - dotDiameter) / 2f;
				using var dotBrush = new SolidBrush(DotColor);
				g.FillEllipse(dotBrush, startX, dotY, dotDiameter, dotDiameter);
			}

			float textX = startX + iconW + iconGap;
			var textRect = new RectangleF(textX, 0, textW + 2f, Height);
			using var sf = new StringFormat(StringFormat.GenericTypographic)
			{
				LineAlignment = StringAlignment.Center
			};
			using (var textBrush = new SolidBrush(ForeColor))
			{
				g.DrawString(Text, Font, textBrush, textRect, sf);
			}
		}

		static GraphicsPath CreateMoonPath(float x, float y, float size)
		{
			var path = new GraphicsPath();
			float s = size / 24f;
			PointF P(float px, float py) => new PointF(x + px * s, y + py * s);

			// SVG: M12 3 c-4.97 0 -9 4.03 -9 9 s4.03 9 9 9 s9 -4.03 9 -9 c0 -.46 -.04 -.92 -.1 -1.36 c-1.14 1.4 -2.88 2.26 -4.8 2.26 c-3.31 0 -6 -2.69 -6 -6 c0 -1.92 .86 -3.66 2.26 -4.8 C12.92 3.04 12.46 3 12 3z
			path.AddBezier(P(12, 3), P(7.03f, 3), P(3, 7.03f), P(3, 12));
			path.AddBezier(P(3, 12), P(3, 16.97f), P(7.03f, 21), P(12, 21));
			path.AddBezier(P(12, 21), P(16.97f, 21), P(21, 16.97f), P(21, 12));
			path.AddBezier(P(21, 12), P(21, 11.54f), P(20.96f, 11.08f), P(20.9f, 10.64f));
			path.AddBezier(P(20.9f, 10.64f), P(19.76f, 12.04f), P(18.02f, 12.9f), P(16.1f, 12.9f));
			path.AddBezier(P(16.1f, 12.9f), P(12.79f, 12.9f), P(10.1f, 10.21f), P(10.1f, 6.9f));
			path.AddBezier(P(10.1f, 6.9f), P(10.1f, 4.98f), P(10.96f, 3.24f), P(12.36f, 2.1f));
			path.AddBezier(P(12.36f, 2.1f), P(12.92f, 3.04f), P(12.46f, 3f), P(12, 3));

			path.CloseFigure();
			return path;
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

	// Filled rounded surface panel behind a group of rows (the "group box" treatment). Anti-aliased for
	// smooth corners; the grey transparency key (see BackdropKey) keeps the AA edge a soft rim
	// instead of a bright fringe.
	void DrawGroupPanel(Graphics g, int top, int height)
	{
		using var b = new SolidBrush(_palette.Surface);
		FillRoundedRect(g, b, new Rectangle(PanelMargin, top, Width - 2 * PanelMargin, height), PanelRadius);
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

	// Needs-you rows first: attention outranks context usage. Stable within each group.
	IEnumerable<SessionModel> OrderedSessions()
	{
		if (_needsYou.Count == 0) return _sessions;
		return _sessions.OrderByDescending(s => s.SessionId is not null && _needsYou.ContainsKey(s.SessionId));
	}
}
