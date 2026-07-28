using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class DetailPanel : Form
{
	const int WS_EX_TOOLWINDOW = 0x00000080;
	const int WS_EX_NOACTIVATE = 0x08000000;
	const int MinWidth = 320;
	const int RowH = 42;
	const int Pad = 12;
	const int QuotaWindowRowH = 42;
	const int QuotaPausedRowH = 18;
	const int GroupVPad = 8;     // inner top/bottom padding inside a group panel
	const int GroupGap = 10;     // vertical gap between the two group panels
	const int PanelMargin = 6;   // horizontal inset of a group panel from the popup edge
	const int PanelRadius = 6;
	const int BottomPillRowH = 26;

	const int WS_EX_TRANSPARENT = 0x00000020;

	private sealed class BackgroundForm : Form
	{
		[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
		public struct POINT { public int x; public int y; }

		[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
		public struct SIZE { public int cx; public int cy; }

		[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential, Pack = 1)]
		public struct BLENDFUNCTION
		{
			public byte BlendOp;
			public byte BlendFlags;
			public byte SourceConstantAlpha;
			public byte AlphaFormat;
		}

		[System.Runtime.InteropServices.DllImport("user32.dll", ExactSpelling = true, SetLastError = true)]
		public static extern bool UpdateLayeredWindow(IntPtr hwnd, IntPtr hdcDst, ref POINT pptDst, ref SIZE psize, IntPtr hdcSrc, ref POINT pptSrc, int crKey, ref BLENDFUNCTION pblend, int dwFlags);

		[System.Runtime.InteropServices.DllImport("user32.dll", ExactSpelling = true, SetLastError = true)]
		public static extern IntPtr GetDC(IntPtr hWnd);

		[System.Runtime.InteropServices.DllImport("user32.dll", ExactSpelling = true)]
		public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

		[System.Runtime.InteropServices.DllImport("gdi32.dll", ExactSpelling = true, SetLastError = true)]
		public static extern IntPtr CreateCompatibleDC(IntPtr hDC);

		[System.Runtime.InteropServices.DllImport("gdi32.dll", ExactSpelling = true, SetLastError = true)]
		public static extern bool DeleteDC(IntPtr hdc);

		[System.Runtime.InteropServices.DllImport("gdi32.dll", ExactSpelling = true)]
		public static extern IntPtr SelectObject(IntPtr hDC, IntPtr hObject);

		[System.Runtime.InteropServices.DllImport("gdi32.dll", ExactSpelling = true, SetLastError = true)]
		public static extern bool DeleteObject(IntPtr hObject);

		public BackgroundForm()
		{
			FormBorderStyle = FormBorderStyle.None;
			ShowInTaskbar = false;
			TopMost = true;
			StartPosition = FormStartPosition.Manual;
		}

		public void PushFrame(Bitmap frame)
		{
			if (!IsHandleCreated) return;
			IntPtr screenDc = GetDC(IntPtr.Zero);
			IntPtr memDc = CreateCompatibleDC(screenDc);
			IntPtr hBmp = IntPtr.Zero;
			IntPtr old = IntPtr.Zero;

			try
			{
				hBmp = frame.GetHbitmap(Color.FromArgb(0));
				old = SelectObject(memDc, hBmp);

				POINT dst = new POINT { x = Left, y = Top };
				SIZE size = new SIZE { cx = frame.Width, cy = frame.Height };
				POINT src = new POINT { x = 0, y = 0 };
				BLENDFUNCTION blend = new BLENDFUNCTION { BlendOp = 0, BlendFlags = 0, SourceConstantAlpha = 255, AlphaFormat = 1 };

				UpdateLayeredWindow(Handle, screenDc, ref dst, ref size, memDc, ref src, 0, ref blend, 2);
			}
			finally
			{
				if (hBmp != IntPtr.Zero)
				{
					SelectObject(memDc, old);
					DeleteObject(hBmp);
				}
				DeleteDC(memDc);
				ReleaseDC(IntPtr.Zero, screenDc);
			}
		}

		protected override bool ShowWithoutActivation => true;

		protected override CreateParams CreateParams
		{
			get { var cp = base.CreateParams; cp.ExStyle |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | 0x80000; return cp; }
		}
	}

	readonly BackgroundForm _bgForm = new();

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
	IReadOnlyList<AntigravityQuotaGroup> _agyGroups = Array.Empty<AntigravityQuotaGroup>();   // touched agy groups only
	DateTime? _lastActivityUtc;   // newest transcript mtime when no session is active; null if never seen

	bool _switchboardEnabled;
	SwitchboardStats? _switchboardStats;   // latest /stats; null means unavailable when enabled
	IReadOnlyDictionary<string, NeedsYouEntry> _needsYou = SwitchboardStats.EmptyNeedsYou;   // last-good needs-you map; held across unavailable polls
	readonly PillButton _switchboardPillButton;

	ClaudeStatusView? _claudeStatus;
	readonly PillButton _claudePillButton;
	AntigravityStatusView? _antigravityStatus;
	readonly PillButton _agyPillButton;
	readonly PillButton _awayPillButton;
	readonly ToolTip _toolTip;

	public event Action? OpenDashboardRequested;
	public event Action? ClaudeStatusButtonClicked;
	public event Action? AntigravityStatusButtonClicked;
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
		Width = MinWidth;
		Visible = false;

		_toolTip = new ToolTip();

		_claudePillButton = new PillButton
		{
			IconType = PillIconType.Claude,
			Text = "",
			Visible = true,
			BackColor = Color.FromArgb(8, 9, 11),
			BorderColor = Color.FromArgb(30, 41, 59),
			SurfaceColor = Color.FromArgb(42, 42, 42),
			ForeColor = Color.FromArgb(99, 109, 125),
			DotColor = Color.FromArgb(154, 160, 166),
		};
		_claudePillButton.Click += (_, _) => OnClaudePillClicked();
		Controls.Add(_claudePillButton);
		_toolTip.SetToolTip(_claudePillButton, "Claude: Status Unknown");

		_switchboardPillButton = new PillButton
		{
			IconType = PillIconType.Switchboard,
			Text = "",
			Visible = true,
		};
		_switchboardPillButton.Click += (_, _) => OpenDashboardRequested?.Invoke();
		Controls.Add(_switchboardPillButton);
		_toolTip.SetToolTip(_switchboardPillButton, "Switchboard Operator");

		_agyPillButton = new PillButton
		{
			IconType = PillIconType.Antigravity,
			Text = "",
			Visible = true,
			BackColor = Color.FromArgb(8, 9, 11),
			BorderColor = Color.FromArgb(30, 41, 59),
			SurfaceColor = Color.FromArgb(42, 42, 42),
			ForeColor = Color.FromArgb(99, 109, 125),
			DotColor = Color.FromArgb(154, 160, 166),
		};
		_agyPillButton.Click += (_, _) => OnAntigravityPillClicked();
		Controls.Add(_agyPillButton);
		_toolTip.SetToolTip(_agyPillButton, "Antigravity: Status Unknown");

		_awayPillButton = new PillButton
		{
			IconType = PillIconType.Moon,
			Text = "",
			Visible = true,
		};
		_awayPillButton.Click += (_, _) => OnAwayPillClicked();
		Controls.Add(_awayPillButton);
		_toolTip.SetToolTip(_awayPillButton, "Toggle Away Mode");
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

	void OnAntigravityPillClicked()
	{
		var level = _antigravityStatus?.DotLevel ?? AntigravityStatusLevel.Unknown;
		bool isGreen = _antigravityStatus is null || level == AntigravityStatusLevel.Operational || level == AntigravityStatusLevel.Unknown;
		if (isGreen)
		{
			AntigravityStatusButtonClicked?.Invoke();
		}
		else
		{
			try
			{
				System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("https://status.cloud.google.com") { UseShellExecute = true });
			}
			catch { }
		}
	}

	static GraphicsPath RoundedRectPath(RectangleF r, float radius)
	{
		float d = radius * 2;
		var path = new GraphicsPath();
		if (d <= 0)
		{
			path.AddRectangle(r);
			return path;
		}
		d = Math.Min(d, Math.Min(r.Width, r.Height));
		if (d <= 0)
		{
			path.AddRectangle(r);
			return path;
		}
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

	protected override void OnVisibleChanged(EventArgs e)
	{
		base.OnVisibleChanged(e);
		if (!Visible) _bgForm.Hide();
	}

	[System.Runtime.InteropServices.DllImport("dwmapi.dll")]
	static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

	protected override void OnHandleCreated(EventArgs e)
	{
		base.OnHandleCreated(e);
		try
		{
			int borderColor = unchecked((int)0xFFFFFFFE); // DWMWA_COLOR_NONE
			DwmSetWindowAttribute(Handle, 34, ref borderColor, 4); // DWMWA_BORDER_COLOR

			int corner = 1; // DWMWCP_DONOTROUND
			DwmSetWindowAttribute(Handle, 33, ref corner, 4); // DWMWA_WINDOW_CORNER_PREFERENCE
		}
		catch { }
	}

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

	// Store the touched agy groups (empty when null/no usage); the popup grows a titled panel per group.
	public void UpdateAntigravityQuota(AntigravityQuotaSummary? summary)
	{
		_agyGroups = summary is null
			? Array.Empty<AntigravityQuotaGroup>()
			: summary.Groups.Where(AntigravityQuota.IsGroupVisible).ToList();
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

		string claudeDetail = view is null || !view.HasData
			? "Status Unknown"
			: (!string.IsNullOrEmpty(view.Description) ? view.Description : "Operational");

		if (view is { IncidentNames.Count: > 0 })
		{
			string incidentsText = string.Join("; ", view.IncidentNames);
			if (string.IsNullOrEmpty(claudeDetail) || claudeDetail.Contains("operational", StringComparison.OrdinalIgnoreCase))
			{
				claudeDetail = incidentsText;
			}
			else if (!claudeDetail.Contains(incidentsText, StringComparison.OrdinalIgnoreCase))
			{
				claudeDetail = claudeDetail + " - " + incidentsText;
			}
		}
		_toolTip.SetToolTip(_claudePillButton, "Claude: " + claudeDetail);

		RecomputeHeight();
		Invalidate();
	}

	// Latest Antigravity status view (published by the server, parsed via AntigravityServerStatus.ParseView).
	public void UpdateAntigravityStatus(AntigravityStatusView view)
	{
		_antigravityStatus = view;
		var level = view?.DotLevel ?? AntigravityStatusLevel.Unknown;
		bool isGreen = view is null || level == AntigravityStatusLevel.Operational || level == AntigravityStatusLevel.Unknown;

		Color dotColor = isGreen
			? (view is { HasData: true } || view is { DotVisible: true } ? StatusColors.Green : _palette.Muted)
			: Palette.ForAntigravityStatus(level);

		Color textColor = !isGreen
			? (level == AntigravityStatusLevel.Minor ? Color.FromArgb(205, 212, 221) : Color.White)
			: Color.FromArgb(99, 109, 125);

		_agyPillButton.DotColor = dotColor;
		_agyPillButton.ForeColor = textColor;
		_agyPillButton.SurfaceColor = _palette.Surface;
		_agyPillButton.BackColor = Color.FromArgb(8, 9, 11);
		_agyPillButton.BorderColor = _palette.Track;

		string agyDetail = view is null || !view.HasData
			? "Status Unknown"
			: (!string.IsNullOrEmpty(view.Description) ? view.Description : "Operational");

		if (view is { IncidentNames.Count: > 0 })
		{
			string incidentsText = string.Join("; ", view.IncidentNames);
			if (string.IsNullOrEmpty(agyDetail) || agyDetail.Contains("operational", StringComparison.OrdinalIgnoreCase))
			{
				agyDetail = incidentsText;
			}
			else if (!agyDetail.Contains(incidentsText, StringComparison.OrdinalIgnoreCase))
			{
				agyDetail = agyDetail + " - " + incidentsText;
			}
		}
		_toolTip.SetToolTip(_agyPillButton, "Antigravity: " + agyDetail);

		RecomputeHeight();
		Invalidate();
	}

	void RecomputeHeight()
	{
		int quotaContentH = (_quota.HasValue ? 2 * QuotaWindowRowH : 0) + (_quotaAuthPaused ? QuotaPausedRowH : 0);
		int quotaH = quotaContentH > 0 ? quotaContentH + 2 * GroupVPad + GroupGap : 0;
		int agyH = _agyGroups.Count * (2 * QuotaWindowRowH + 2 * GroupVPad + GroupGap);
		int ctxH = Math.Max(1, _sessions.Count) * RowH + 2 * GroupVPad;
		int group3H = BottomPillRowH + 2 * GroupVPad;
		Height = Pad + quotaH + agyH + ctxH + GroupGap + group3H + Pad;
	}

	public void ShowAbove(Rectangle widgetScreenBounds)
	{
		int targetWidth = Math.Max(MinWidth, widgetScreenBounds.Width);
		if (Width != targetWidth)
		{
			Width = targetWidth;
		}
		int x = Math.Max(0, widgetScreenBounds.Right - Width);
		int y = widgetScreenBounds.Top - Height;   // touch the widget's top edge so hover doesn't break crossing a gap
		Location = new Point(x, y);
		_bgForm.Size = Size;
		_bgForm.Location = Location;

		if (!_bgForm.Visible)
		{
			int stripW = Width;
			int stripH = Height + 300;
			int stripY = widgetScreenBounds.Top - stripH;
			if (stripY < 0)
			{
				stripH += stripY;
				stripY = 0;
			}
			if (stripW <= 0 || stripH <= 0) return;

			using var materialBmp = new Bitmap(stripW, stripH, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
			using (var g = Graphics.FromImage(materialBmp))
			{
				g.CopyFromScreen(x, stripY, 0, 0, new Size(stripW, stripH));
			}

			var rect = new Rectangle(0, 0, stripW, stripH);
			var data = materialBmp.LockBits(rect, System.Drawing.Imaging.ImageLockMode.ReadWrite, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
			int len = stripW * stripH;
			int[] pixels = new int[len];
			if (data.Stride == stripW * 4)
			{
				System.Runtime.InteropServices.Marshal.Copy(data.Scan0, pixels, 0, len);
			}
			else
			{
				for (int i = 0; i < stripH; i++)
					System.Runtime.InteropServices.Marshal.Copy(data.Scan0 + i * data.Stride, pixels, i * stripW, stripW);
			}

			FrostMaterial.ApplyFrost(pixels, stripW, stripH);

			if (data.Stride == stripW * 4)
			{
				System.Runtime.InteropServices.Marshal.Copy(pixels, 0, data.Scan0, len);
			}
			else
			{
				for (int i = 0; i < stripH; i++)
					System.Runtime.InteropServices.Marshal.Copy(pixels, i * stripW, data.Scan0 + i * data.Stride, stripW);
			}
			materialBmp.UnlockBits(data);

			using var targetBmp = new Bitmap(Width, Height, System.Drawing.Imaging.PixelFormat.Format32bppPArgb);
			using (var g = Graphics.FromImage(targetBmp))
			{
				g.Clear(Color.Transparent);
				g.SmoothingMode = SmoothingMode.AntiAlias;
				using var path = RoundedRectPath(new Rectangle(0, 0, Width, Height), 8);
				using var brush = new TextureBrush(materialBmp);
				// Offset brush so it grabs the bottom 'Height' portion of the strip
				brush.TranslateTransform(0, -(stripH - Height));
				g.FillPath(brush, path);
			}

			_bgForm.PushFrame(targetBmp);
			_bgForm.Show();
		}

		_bgForm.BringToFront();
		if (!Visible) Show();
		BringToFront();
	}

	// Friendly popup label for an Antigravity group, keyed off the server's group name.
	static string AgyLabel(AntigravityQuotaGroup g)
	{
		string d = g.DisplayName ?? "";
		if (d.Contains("Gemini", StringComparison.OrdinalIgnoreCase)) return "Antigravity w/ Gemini";
		if (d.Contains("Claude", StringComparison.OrdinalIgnoreCase)) return "Antigravity w/ Claude";
		return "Antigravity - " + d;
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

		// Antigravity groups first (order: w/ Claude, then w/ Gemini), each a header-less pill with fully-labelled 5h / 7d rows.
		foreach (var group in _agyGroups.OrderBy(AntigravityQuota.GroupSortKey))
		{
			int groupH = 2 * QuotaWindowRowH + 2 * GroupVPad;
			DrawGroupPanel(g, y, groupH);
			int inner = y + GroupVPad;
			string name = AgyLabel(group);
			inner = DrawQuotaWindow(g, inner, "5h - " + name, AntigravityQuota.ToUsedWindow(group, "5h"), QuotaPacing.SessionDuration, label, small);
			DrawQuotaWindow(g, inner, "7d - " + name, AntigravityQuota.ToUsedWindow(group, "weekly"), QuotaPacing.WeeklyDuration, label, small);
			y += groupH + GroupGap;
		}

		// Claude Code plan-usage (5h / 7d) below the Antigravity pills, plus the auth-paused banner when polling is backed off.
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
				inner = DrawQuotaWindow(g, inner, "5h - Claude Code", q.Session, QuotaPacing.SessionDuration, label, small);
				DrawQuotaWindow(g, inner, "7d - Claude Code", q.Weekly, QuotaPacing.WeeklyDuration, label, small);
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

			// line 1: [WSL] label model .................... tokens ratio (e.g. 260K / 1.0M)
			int labelX = Pad + 16;
			if (s.Distro is not null)
			{
				g.DrawString("WSL", small, mutedBrush, labelX, y + 2);
				labelX += (int)Math.Ceiling(g.MeasureString("WSL", small).Width) + 6;
			}
			var tokens = s.IsError ? "?" : $"{Human(s.ContextTokens)} / {Human(s.WindowSize)}";
			var tokensSize = g.MeasureString(tokens, small);
			g.DrawString(tokens, small, mutedBrush, Width - Pad - tokensSize.Width, y + 1);

			string modelStr = "w/ " + ShortModel(s.Model);
			var modelSize = g.MeasureString(modelStr, small, PointF.Empty, StringFormat.GenericTypographic);
			float maxLine1W = Width - Pad - tokensSize.Width - labelX - 6;
			float maxCwdW = maxLine1W - modelSize.Width - 5;
			string displayLabel = Ellipsize(g, s.Label, label, maxCwdW);
			g.DrawString(displayLabel, label, textBrush, labelX, y);
			float cwdW = g.MeasureString(displayLabel, label, PointF.Empty, StringFormat.GenericTypographic).Width;
			g.DrawString(modelStr, small, mutedBrush, labelX + cwdW + 5, y + 1);

			// line 2: bar + pct
			int barY = y + 22;
			int barW = Width - Pad * 2 - 42;
			using var trackPath = RoundedRectPath(new RectangleF(Pad, barY, barW, 8), 4);
			using (var track = new SolidBrush(_palette.Track))
				g.FillPath(track, trackPath);
			if (!s.IsError && s.Pct > 0)
			{
				using var gradientFill = new LinearGradientBrush(new Rectangle(Pad, barY, Math.Max(1, barW), 8), Color.White, Color.Black, LinearGradientMode.Horizontal);
				var blend = new ColorBlend(3)
				{
					Colors = new[] { StatusColors.Green, StatusColors.Amber, StatusColors.Red },
					Positions = new[] { 0f, 0.6f, 1f }
				};
				gradientFill.InterpolationColors = blend;
				int fillW = Math.Max(1, (int)Math.Round(barW * Math.Clamp(s.Pct, 0, 1)));
				using var fillPath = RoundedRectPath(new RectangleF(Pad, barY, fillW, 8), 4);
				g.FillPath(gradientFill, fillPath);
			}
			var pct = s.IsError ? "?" : $"{(int)Math.Round(s.Pct * 100)}%";
			using var pctBrush = new SolidBrush(s.IsError ? _palette.Warning : SeverityGradient.For(s.Pct));
			g.DrawString(pct, label, pctBrush, Width - Pad - 34, barY - 4);

			y += RowH;
		}

		// Group 3: Group panel containing Away, Claude, Switchboard & Antigravity indicator pills.
		int group3Top = ctxTop + ctxH + GroupGap;
		int group3H = BottomPillRowH + 2 * GroupVPad;
		DrawGroupPanel(g, group3Top, group3H);

		int btn3Y = group3Top + GroupVPad;
		int padX = 14;
		int btnGap = 6;
		int availableW = Width - 2 * padX - 3 * btnGap;
		int btnW = availableW / 4;

		_agyPillButton.Location = new Point(padX, btn3Y);
		_agyPillButton.Size = new Size(btnW, BottomPillRowH);

		_claudePillButton.Location = new Point(padX + btnW + btnGap, btn3Y);
		_claudePillButton.Size = new Size(btnW, BottomPillRowH);

		_switchboardPillButton.Location = new Point(padX + 2 * (btnW + btnGap), btn3Y);
		_switchboardPillButton.Size = new Size(btnW, BottomPillRowH);

		_awayPillButton.Location = new Point(padX + 3 * (btnW + btnGap), btn3Y);
		_awayPillButton.Size = new Size(btnW, BottomPillRowH);
	}

	internal enum PillIconType
	{
		None,
		Moon,
		Claude,
		Switchboard,
		Antigravity
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
		public PillIconType IconType { get; set; } = PillIconType.None;

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

			float textW = string.IsNullOrEmpty(Text) ? 0f : g.MeasureString(Text, Font, PointF.Empty, StringFormat.GenericTypographic).Width;
			float iconW = IconType switch
			{
				PillIconType.Moon => 13f,
				PillIconType.Claude => 12f,
				PillIconType.Switchboard => 12f,
				PillIconType.Antigravity => 12f,
				_ => 0f,
			};

			float iconGap = (iconW > 0 && textW > 0) ? 6f : 0f;
			float contentW = iconW + iconGap + textW;
			float startX = (Width - contentW) / 2f;

			float currentX = startX;

			if (IconType == PillIconType.Moon)
			{
				float moonSize = 13f;
				float moonY = (Height - moonSize) / 2f;
				using var moonPath = CreateMoonPath(currentX, moonY, moonSize);
				using var moonBrush = new SolidBrush(MoonIconColor);
				g.FillPath(moonBrush, moonPath);
				currentX += moonSize + iconGap;
			}
			else if (IconType == PillIconType.Claude)
			{
				float iconSize = 12f;
				float iconY = (Height - iconSize) / 2f;
				using var claudePath = CreateClaudePath(currentX, iconY, iconSize);
				using var iconBrush = new SolidBrush(DotColor);
				g.FillPath(iconBrush, claudePath);
				currentX += iconSize + iconGap;
			}
			else if (IconType == PillIconType.Antigravity)
			{
				float iconSize = 12f;
				float iconY = (Height - iconSize) / 2f;
				using var agyPath = CreateAntigravityPath(currentX, iconY, iconSize);
				using var iconBrush = new SolidBrush(DotColor);
				g.FillPath(iconBrush, agyPath);
				currentX += iconSize + iconGap;
			}
			else if (IconType == PillIconType.Switchboard)
			{
				float iconSize = 12f;
				float iconY = (Height - iconSize) / 2f;
				using var iconBrush = new SolidBrush(DotColor);
				DrawSwitchboardIcon(g, iconBrush, currentX, iconY, iconSize);
				currentX += iconSize + iconGap;
			}

			if (textW > 0)
			{
				var textRect = new RectangleF(currentX, 0, textW + 2f, Height);
				using var sf = new StringFormat(StringFormat.GenericTypographic)
				{
					LineAlignment = StringAlignment.Center
				};
				using var textBrush = new SolidBrush(ForeColor);
				g.DrawString(Text, Font, textBrush, textRect, sf);
			}
		}

		static GraphicsPath CreateClaudePath(float x, float y, float size)
		{
			var path = new GraphicsPath();
			float cx = x + size / 2f;
			float cy = y + size / 2f;
			float r1 = size / 2f;
			float r2 = size * 0.22f;

			PointF[] pts = new PointF[16];
			for (int i = 0; i < 16; i++)
			{
				double angle = i * Math.PI / 8.0 - Math.PI / 2.0;
				double r = (i % 2 == 0) ? r1 : ((i % 4 == 1 || i % 4 == 3) ? r1 * 0.45 : r2);
				pts[i] = new PointF((float)(cx + r * Math.Cos(angle)), (float)(cy + r * Math.Sin(angle)));
			}
			path.AddPolygon(pts);
			return path;
		}

		static GraphicsPath CreateAntigravityPath(float x, float y, float size)
		{
			var path = new GraphicsPath();
			float cx = x + size / 2f;
			float cy = y + size / 2f;
			float r = size / 2f;

			PointF top = new PointF(cx, cy - r);
			PointF right = new PointF(cx + r, cy);
			PointF bottom = new PointF(cx, cy + r);
			PointF left = new PointF(cx - r, cy);

			path.AddBezier(top, new PointF(cx + r * 0.35f, cy - r * 0.35f), new PointF(cx + r * 0.35f, cy - r * 0.35f), right);
			path.AddBezier(right, new PointF(cx + r * 0.35f, cy + r * 0.35f), new PointF(cx + r * 0.35f, cy + r * 0.35f), bottom);
			path.AddBezier(bottom, new PointF(cx - r * 0.35f, cy + r * 0.35f), new PointF(cx - r * 0.35f, cy + r * 0.35f), left);
			path.AddBezier(left, new PointF(cx - r * 0.35f, cy - r * 0.35f), new PointF(cx - r * 0.35f, cy - r * 0.35f), top);

			path.CloseFigure();
			return path;
		}

		static void DrawSwitchboardIcon(Graphics g, Brush brush, float x, float y, float size)
		{
			float trackW = 1.75f;
			float knobR = 2.25f;

			float t1X = x + size * 0.3f;
			float t2X = x + size * 0.7f;

			g.FillRectangle(brush, t1X - trackW / 2f, y + 1f, trackW, size - 2f);
			g.FillRectangle(brush, t2X - trackW / 2f, y + 1f, trackW, size - 2f);

			g.FillEllipse(brush, t1X - knobR, y + size * 0.3f - knobR, knobR * 2f, knobR * 2f);
			g.FillEllipse(brush, t2X - knobR, y + size * 0.7f - knobR, knobR * 2f, knobR * 2f);
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

		using var textBrush = new SolidBrush(_palette.Text);
		using var mutedBrush = new SolidBrush(_palette.Muted);

		// line 1: window name (left) + exact reset time (right)
		int wIndex = name.IndexOf(" w/ ", StringComparison.Ordinal);
		if (wIndex >= 0)
		{
			string mainPart = name.Substring(0, wIndex);
			string subPart = name.Substring(wIndex + 1);
			g.DrawString(mainPart, label, textBrush, Pad, y);
			float mainW = g.MeasureString(mainPart, label, PointF.Empty, StringFormat.GenericTypographic).Width;
			g.DrawString(subPart, small, mutedBrush, Pad + mainW + 5, y + 1);
		}
		else
		{
			g.DrawString(name, label, textBrush, Pad, y);
		}
		string reset = QuotaFormat.FormatResetTime(w.ResetsAt, now);
		if (reset.Length > 0)
		{
			string resetText = "resets " + reset;
			var rs = g.MeasureString(resetText, small);
			g.DrawString(resetText, small, mutedBrush, Width - Pad - rs.Width, y + 2);
		}

		// line 2: segmented usage bar (severity gradient)
		int barY = y + 22;
		int barW = Width - Pad * 2 - 42;
		int segmentCount = duration == QuotaPacing.SessionDuration ? 5 : 7;
		int segGap = 2;
		
		using var trackPath = RoundedRectPath(new RectangleF(Pad, barY, barW, 8), 4);
		using (var track = new SolidBrush(_palette.Track))
			g.FillPath(track, trackPath);

		if (w.Percentage > 0)
		{
			using var gradientFill = new LinearGradientBrush(new Rectangle(Pad, barY, Math.Max(1, barW), 8), Color.White, Color.Black, LinearGradientMode.Horizontal);
			var blend = new ColorBlend(3)
			{
				Colors = new[] { StatusColors.Green, StatusColors.Amber, StatusColors.Red },
				Positions = new[] { 0f, 0.6f, 1f }
			};
			gradientFill.InterpolationColors = blend;
			
			int fillW = Math.Max(1, (int)Math.Round(barW * Math.Clamp(w.Percentage / 100.0, 0, 1)));
			using var fillPath = RoundedRectPath(new RectangleF(Pad, barY, fillW, 8), 4);
			g.FillPath(gradientFill, fillPath);
		}

		// Draw gaps over the bar to create segments
		using (var bgBrush = new SolidBrush(_palette.Background))
		{
			int currentX = Pad;
			for (int i = 0; i < segmentCount - 1; i++)
			{
				int totalAvailable = barW - (segmentCount - 1) * segGap;
				int segW = totalAvailable / segmentCount;
				if (i < totalAvailable % segmentCount) segW++;
				
				currentX += segW;
				g.FillRectangle(bgBrush, currentX, barY, segGap, 8);
				currentX += segGap;
			}
		}

		// ghost pace bar beneath: fill to the elapsed-time fraction, amber when burning over pace
		// (matching the caption tint), muted otherwise. Omitted when reset unknown.
		int ghostY = barY + 10;
		if (pace.ElapsedFraction is double ef)
		{
			using var ghostTrackPath = RoundedRectPath(new RectangleF(Pad, ghostY, barW, 3), 1.5f);
			using var ghostTrack = new SolidBrush(_palette.Track);
			g.FillPath(ghostTrack, ghostTrackPath);
			var ghostColor = pace.Verdict == PaceVerdict.Over ? _palette.Warning : _palette.Muted;
			using var ghostFill = new SolidBrush(ghostColor);
			int ghostFillW = Math.Max(1, (int)Math.Round(barW * ef));
			using var ghostFillPath = RoundedRectPath(new RectangleF(Pad, ghostY, ghostFillW, 3), 1.5f);
			g.FillPath(ghostFill, ghostFillPath);
		}

		string pctText = $"{(int)Math.Round(w.Percentage)}%";
		using (var pctBrush = new SolidBrush(SeverityGradient.For(Math.Clamp(w.Percentage / 100.0, 0, 1))))
			g.DrawString(pctText, label, pctBrush, Width - Pad - 34, barY - 4);

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
		if (model.Contains("fable", StringComparison.OrdinalIgnoreCase)) return "Fable";
		if (model.Contains("opus", StringComparison.OrdinalIgnoreCase)) return "Opus";
		if (model.Contains("sonnet", StringComparison.OrdinalIgnoreCase)) return "Sonnet";
		if (model.Contains("haiku", StringComparison.OrdinalIgnoreCase)) return "Haiku";
		if (model.Contains("gemini", StringComparison.OrdinalIgnoreCase))
		{
			if (model.Contains("flash", StringComparison.OrdinalIgnoreCase)) return "Gemini Flash";
			if (model.Contains("pro", StringComparison.OrdinalIgnoreCase)) return "Gemini Pro";
			return "Gemini";
		}
		return model.Length > 12 ? model.Substring(0, 10) + "..." : model;
	}

	// Needs-you rows first: attention outranks context usage. Stable within each group.
	IEnumerable<SessionModel> OrderedSessions()
	{
		if (_needsYou.Count == 0) return _sessions;
		return _sessions.OrderByDescending(s => s.SessionId is not null && _needsYou.ContainsKey(s.SessionId));
	}
}
