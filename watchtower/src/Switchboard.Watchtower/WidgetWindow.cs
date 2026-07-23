using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.Linq;
using System.Runtime.InteropServices;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class WidgetWindow : Form
{
	const int DragThreshold = 4;
	const float RingThickness = 3f;     // pen width; MUST match ContextRingLayout's thickness arg
	const float RingGap = 0.5f;         // minimal gap between concentric rings, for separation
	const int RingMaxCount = 3;         // cap visible rings so each gets room at this size; rest roll into "+K"
	const int RingClusterW = 28;        // == ContextRingLayout dMax cap; horizontal room for the rings
	const int OverflowTextRoom = 14;    // room to the right of the cluster for the "+K" indicator
	const int GrabW = 14;          // left grab-handle strip; the ONLY drag target
	const int PadAfterGrab = 4;
	const int RightMargin = 8;     // small trailing gap (the max-% label is gone)

	// Quota block (5h/7d rows): a 10-segment usage bar with a thin muted pace (elapsed-time) bar beneath.
	const int QSegW = 9, QSegGap = 1, QSegCount = 10, QSegH = 8;
	const int QBarW = QSegCount * (QSegW + QSegGap) - QSegGap;          // 49
	const int QPaceH = 2;                                               // pace (elapsed-time) bar height (skinny + calm on the widget)
	const int QPaceGap = 2;                                             // gap between the usage bar and the pace bar
	const int QSep = 14;                                                // gap before the context rings
	const int QuotaBlockW = QBarW + QSep;
	const int HeightWithQuota = 44;
	const int HeightContextOnly = 34;

	IReadOnlyList<SessionModel> _sessions = Array.Empty<SessionModel>();
	Palette _palette = new(light: false);
	bool _light;
	bool _clearType = true;   // true = opaque theme bg + ClearType text (monitor style, default); false = true per-pixel-alpha transparency
	QuotaUsage? _quota;       // latest Claude plan usage (5h/7d); null until first successful poll
	IReadOnlyList<AntigravityQuotaGroup> _agyGroups = Array.Empty<AntigravityQuotaGroup>();
	bool _showQuota = true;   // user preference; the block also requires _quota to have a value
	bool _showBadge;          // Switchboard ShowBadge preference
	bool _hasPending;         // Switchboard has unanswered questions -> draw the amber badge
	bool _claudeDotVisible;
	ClaudeStatusLevel _claudeLevel = ClaudeStatusLevel.Operational;
	bool _claudePulse;        // animate the status dot (active incident) vs hold steady (resolved)
	float _claudePhase;       // 0..1 pulse phase, advanced by the host's pulse timer

	bool QuotaVisible => _showQuota && _quota.HasValue;
	public QuotaUsage? Quota => _quota;

	// One quota "set" per visible source: the Claude set (when shown) + each touched agy group.
	int VisibleSetCount => (QuotaVisible ? 1 : 0) + _agyGroups.Count;

	Point _dragStart;
	bool _pressed;
	bool _dragging;

	IntPtr _taskbar;
	IntPtr _winEventHook;
	bool _embedded;                                // true once reparented as a WS_CHILD of the taskbar
	nint _embeddedHandle;   // HWND at the last successful embed; a mismatch on detach = WinForms recreated it
	bool _ulwFailing;       // last UpdateLayeredWindow outcome, so failures log once per streak
	readonly uint _taskbarCreatedMsg;
	readonly Native.WinEventProc _winEventCb; // kept in a field so the GC cannot collect the callback
	System.Drawing.Rectangle _lastTbRect = System.Drawing.Rectangle.Empty;  // taskbar rect at last reposition

	public event Action<int>? PositionChanged;    // new screen X persisted by the host
	public Action<string>? Diagnostic;             // optional sink for embed/lifecycle diagnostics

	[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
	public int? PreferredX { get; set; }          // right-edge screen X from config; null = auto (left of tray)

	public WidgetWindow()
	{
		FormBorderStyle = FormBorderStyle.None;
		ShowInTaskbar = false;
		TopMost = true;
		StartPosition = FormStartPosition.Manual;
		Height = 34;
		Width = 90;
		_winEventCb = OnTrayLocationChanged;
		_taskbarCreatedMsg = Native.RegisterWindowMessage("TaskbarCreated");
	}

	protected override bool ShowWithoutActivation => true;

	protected override CreateParams CreateParams
	{
		get
		{
			var cp = base.CreateParams;
			cp.ExStyle |= Native.WS_EX_TOOLWINDOW | Native.WS_EX_NOACTIVATE | Native.WS_EX_LAYERED;
			return cp;
		}
	}

	// Layered windows are painted via UpdateLayeredWindow, not WM_PAINT.
	protected override void OnPaintBackground(PaintEventArgs e) { }

	public void AttachToTaskbar()
	{
		if (!IsHandleCreated) return;
		_taskbar = Native.FindWindow("Shell_TrayWnd", null);

		// Try to embed as a true child of the taskbar (the CodeZeno-monitor behavior). If SetParent
		// fails, we stay a top-most overlay - the proven fallback - and everything else still works.
		if (_taskbar != IntPtr.Zero)
			TryEmbed();

		// The hook dies with explorer's thread but the handle stays non-zero, so always
		// unhook before re-registering against the (possibly new) taskbar thread.
		if (_winEventHook != IntPtr.Zero) { Native.UnhookWinEvent(_winEventHook); _winEventHook = IntPtr.Zero; }
		if (_taskbar != IntPtr.Zero)
		{
			uint thread = Native.GetWindowThreadProcessId(_taskbar, out _);
			_winEventHook = Native.SetWinEventHook(
				Native.EVENT_OBJECT_LOCATIONCHANGE, Native.EVENT_OBJECT_LOCATIONCHANGE,
				IntPtr.Zero, _winEventCb, 0, thread, Native.WINEVENT_OUTOFCONTEXT);
		}
		// Explorer-restart recovery of the embedded child otherwise rides the implicit
		// WinForms child-HWND recreation path (TaskbarCreated does not reach an embedded child).
		PositionOverTaskbar();
		if (_embedded)
			Diagnostic?.Invoke($"re-attach complete: visible={Native.IsWindowVisible(Handle)}");
	}

	// Reparent this window into the taskbar following the exact sequence the CodeZeno monitor uses:
	// preserve WS_EX_LAYERED and add tool-window/no-activate, flip WS_POPUP -> WS_CHILD|WS_CLIPSIBLINGS,
	// refresh the frame, then SetParent last. Style is changed via SetWindowLong AFTER handle creation
	// (NOT CreateParams) because a top-level WinForms Form ignores WS_CHILD in CreateParams.
	void TryEmbed()
	{
		if (_embedded && Native.GetParent(Handle) == _taskbar) return;

		uint origEx = (uint)Native.GetWindowLongPtr(Handle, Native.GWL_EXSTYLE);
		uint origStyle = (uint)Native.GetWindowLongPtr(Handle, Native.GWL_STYLE);

		Native.SetWindowLongPtr(Handle, Native.GWL_EXSTYLE,
			(nint)(origEx | (uint)Native.WS_EX_TOOLWINDOW | (uint)Native.WS_EX_NOACTIVATE));

		uint newStyle = (origStyle & ~unchecked((uint)Native.WS_POPUP)) | (uint)Native.WS_CHILD | (uint)Native.WS_CLIPSIBLINGS;
		Native.SetWindowLongPtr(Handle, Native.GWL_STYLE, (nint)newStyle);

		// Force the frame change to take effect before reparenting (the Rust app gets away without this
		// because it is a raw window; a WinForms Form needs the explicit SWP_FRAMECHANGED refresh).
		Native.SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0,
			Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_FRAMECHANGED | Native.SWP_NOACTIVATE);

		Native.SetParent(Handle, _taskbar);
		_embedded = Native.GetParent(Handle) == _taskbar;  // reliable success test; SetParent's NULL return is ambiguous here
		if (_embedded)
		{
			TopMost = false; // meaningless and counter-productive for a child window
			_embeddedHandle = Handle;
		}
		else
		{
			// SetParent failed: restore the original styles so the top-most overlay fallback behaves correctly.
			Native.SetWindowLongPtr(Handle, Native.GWL_EXSTYLE, (nint)origEx);
			Native.SetWindowLongPtr(Handle, Native.GWL_STYLE, (nint)origStyle);
			Native.SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0,
				Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_FRAMECHANGED | Native.SWP_NOACTIVATE);
		}

		Diagnostic?.Invoke($"embed attempt: taskbar={_taskbar:X} embedded={_embedded} handle=0x{Handle:X}"
			+ $" visible={Native.IsWindowVisible(Handle)}"
			+ $" style=0x{(uint)Native.GetWindowLongPtr(Handle, Native.GWL_STYLE):X8}"
			+ $" ex=0x{(uint)Native.GetWindowLongPtr(Handle, Native.GWL_EXSTYLE):X8}");
	}

	// The window's true on-screen rectangle. Form.Bounds is parent-relative (and stale) once embedded,
	// so hover hit-testing and the detail panel must use this instead.
	public Rectangle ScreenBounds =>
		IsHandleCreated && Native.GetWindowRect(Handle, out var r)
			? Rectangle.FromLTRB(r.Left, r.Top, r.Right, r.Bottom)
			: Bounds;

	int CurrentScreenRight() => IsHandleCreated && Native.GetWindowRect(Handle, out var r) ? r.Right : Right;

	public void KeepOnTop()
	{
		if (_pressed) { if (!_embedded) Raise(); return; }

		// If we were embedded and lost our parent (explorer restart / taskbar recreated), re-attach.
		if (_embedded && Native.GetParent(Handle) != _taskbar)
		{
			nint h = Handle;
			Diagnostic?.Invoke(h == _embeddedHandle
				? $"embedded child detached from taskbar; re-attaching (handle=0x{h:X} unchanged)"
				: $"embedded child detached from taskbar; re-attaching (handle recreated 0x{_embeddedHandle:X} -> 0x{h:X})");
			_embedded = false;
			AttachToTaskbar();
			return;
		}

		// A child moves with its parent, so it needs no per-tick re-raise; the WinEvent hook handles
		// taskbar resize / tray moves. The overlay still re-asserts position + topmost each tick.
		if (!_embedded)
			PositionOverTaskbar();
	}

	public void PositionOverTaskbar()
	{
		if (!TaskbarLocator.TryGetTaskbarRect(out var tb)) { if (!_embedded) Raise(); Render(); return; }
		_lastTbRect = tb;

		int? trayLeft = TaskbarLocator.TryGetTrayRect(out var tray) ? tray.Left : null;
		var place = TaskbarPlacement.Compute(tb, trayLeft, Width, Height, PreferredX, _embedded);

		if (_embedded)
			Native.MoveWindow(Handle, place.X, place.Y, Width, Height, true);
		else
		{
			Location = new Point(place.X, place.Y);
			Raise();
		}
		Render();
	}

	void Raise()
	{
		if (IsHandleCreated)
			Native.SetWindowPos(Handle, Native.HWND_TOPMOST, 0, 0, 0, 0, Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
	}

	void OnTrayLocationChanged(IntPtr hHook, uint ev, IntPtr hwnd, int idObject, int idChild, uint thread, uint time)
	{
		try { if (IsHandleCreated) BeginInvoke(new Action(RepositionIfTaskbarMoved)); }
		catch { /* shutting down */ }
	}

	// LOCATIONCHANGE is scoped to the whole explorer thread, so it fires per tooltip/animation.
	// Only reposition (and rebuild the layered bitmap) when the taskbar rect actually changed.
	void RepositionIfTaskbarMoved()
	{
		if (TaskbarLocator.TryGetTaskbarRect(out var tb) && tb == _lastTbRect) return;
		PositionOverTaskbar();
	}

	protected override void WndProc(ref Message m)
	{
		if (_taskbarCreatedMsg != 0 && (uint)m.Msg == _taskbarCreatedMsg)
		{
			_taskbar = IntPtr.Zero;
			AttachToTaskbar();
		}
		base.WndProc(ref m);
	}

	public void UpdateSessions(IReadOnlyList<SessionModel> sessions, bool lightTheme)
	{
		_sessions = sessions;
		_light = lightTheme;
		_palette = new Palette(lightTheme);
		RecomputeSize();
		PositionOverTaskbar();
	}

	// Store the latest plan usage and re-lay-out (the quota block changes the widget's size).
	public void UpdateQuota(QuotaUsage usage)
	{
		_quota = usage;
		RecomputeSize();
		PositionOverTaskbar();
	}

	public void SetShowQuota(bool show)
	{
		_showQuota = show;
		RecomputeSize();
		PositionOverTaskbar();
	}

	// Store only the touched groups (untouched agy groups are hidden per the visibility rule).
	public void UpdateAntigravityQuota(AntigravityQuotaSummary? summary)
	{
		_agyGroups = summary is null
			? Array.Empty<AntigravityQuotaGroup>()
			: summary.Groups.Where(AntigravityQuota.IsGroupVisible).ToList();
		RecomputeSize();
		PositionOverTaskbar();
	}

	// Show/clear the Switchboard pending badge (an amber dot), mirroring the tray icon.
	// No size change, so this only repaints.
	public void SetPending(bool showBadge, bool hasPending)
	{
		if (_showBadge == showBadge && _hasPending == hasPending) return;
		_showBadge = showBadge;
		_hasPending = hasPending;
		Render();
	}

	// Show/hide the sticky Claude status dot (centered in the ring cluster). Visible whenever the watch
	// is not Idle; color reflects the latest known status; pulses while an incident is active.
	public void SetClaudeStatus(bool visible, ClaudeStatusLevel level)
	{
		bool pulse = visible && level is ClaudeStatusLevel.Minor or ClaudeStatusLevel.Major or ClaudeStatusLevel.Critical;
		if (_claudeDotVisible == visible && _claudeLevel == level && _claudePulse == pulse) return;
		_claudeDotVisible = visible;
		_claudeLevel = level;
		_claudePulse = pulse;
		Render();
	}

	// True while the status dot should animate; the host runs a timer that calls TickClaudePulse.
	public bool ClaudePulsing => _claudePulse;

	// Advance the pulse one frame and repaint. No-op when not pulsing.
	public void TickClaudePulse()
	{
		if (!_claudePulse) return;
		_claudePhase = (_claudePhase + 0.048f) % 1f;
		Render();
	}

	// Re-render only (no reposition): the countdown text is recomputed from the stored reset times at
	// paint time, so the host can tick this faster than the poll without refetching.
	public void RefreshQuotaCountdown()
	{
		if (QuotaVisible || _agyGroups.Count > 0) Render();
	}

	void RecomputeSize()
	{
		int quotaW = VisibleSetCount * QuotaBlockW;
		int cluster = RingClusterW + OverflowTextRoom;   // fixed: no longer scales with session count
		Width = Math.Max(72, GrabW + quotaW + PadAfterGrab + cluster + RightMargin);
		Height = VisibleSetCount > 0 ? HeightWithQuota : HeightContextOnly;
	}

	protected override void OnMouseDown(MouseEventArgs e)
	{
		// Only the grab handle initiates a drag.
		if (e.Button == MouseButtons.Left && e.X < GrabW)
		{
			_pressed = true;
			_dragging = false;
			_dragStart = e.Location;
		}
		base.OnMouseDown(e);
	}

	protected override void OnMouseMove(MouseEventArgs e)
	{
		Cursor = e.X < GrabW ? Cursors.SizeWE : Cursors.Default;

		if (_pressed)
		{
			if (!_dragging && Math.Abs(e.X - _dragStart.X) > DragThreshold)
				_dragging = true;

			if (_dragging && TaskbarLocator.TryGetTaskbarRect(out var tb))
			{
				// Work in screen coordinates (Left is parent-relative/stale once embedded); let
				// PositionOverTaskbar convert to parent-relative as needed. Clamp left of the tray.
				int? trayLeft = TaskbarLocator.TryGetTrayRect(out var tray) ? tray.Left : null;
				int desiredRightX = CurrentScreenRight() + (e.X - _dragStart.X);
				PreferredX = TaskbarPlacement.ClampScreenRightX(desiredRightX, tb, trayLeft, Width);
				PositionOverTaskbar();
			}
		}
		base.OnMouseMove(e);
	}

	protected override void OnMouseUp(MouseEventArgs e)
	{
		if (e.Button == MouseButtons.Left && _pressed)
		{
			_pressed = false;
			if (_dragging && PreferredX is int fx)
				PositionChanged?.Invoke(fx);  // PreferredX is the clamped screen X set during the drag
		}
		base.OnMouseUp(e);
	}

	// Switch between true-transparency and opaque-ClearType rendering and repaint immediately.
	public void SetRenderMode(bool clearType)
	{
		_clearType = clearType;
		Render();
	}

	// The opaque theme background used by the ClearType mode (and as the alpha-mask key). Approximates
	// the Win11 taskbar - the CodeZeno monitor hardcodes these same two constants by light/dark.
	Color ThemeBackground => _light ? Color.FromArgb(255, 0xF3, 0xF3, 0xF3) : Color.FromArgb(255, 0x1C, 0x1C, 0x1C);

	// Build a 32bpp premultiplied-ARGB bitmap and push it via UpdateLayeredWindow.
	void Render()
	{
		if (!IsHandleCreated) return;

		Color bg = ThemeBackground;
		using var bmp = new Bitmap(Width, Height, PixelFormat.Format32bppArgb);
		using (var g = Graphics.FromImage(bmp))
		{
			g.SmoothingMode = SmoothingMode.AntiAlias;
			if (_clearType)
			{
				// Opaque theme background lets GDI+ use sub-pixel ClearType for crisp, OS-native text.
				g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;
				g.Clear(bg);
			}
			else
			{
				g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
				g.Clear(Color.Transparent);
			}
			DrawContent(g);
		}

		// ClearType mode fakes transparency: background pixels -> alpha 1 (near-invisible but still
		// hit-testable), everything else -> opaque. After Premultiply, alpha-1 pixels collapse to ~0.
		if (_clearType)
			ApplyOpaqueAlphaMask(bmp, bg);

		Premultiply(bmp);
		SetBitmap(bmp);
	}

	// Rings-only palette: green -> true yellow -> red. The endpoints match SeverityGradient's green and
	// red (so a near-full context reads the same bright red here and in the popup), while the yellow
	// midpoint separates the mid/high bands that the shared orange-amber knee renders too alike at this
	// size. Quota bars and the tray icon still use SeverityGradient directly.
	static readonly Color RingLow = StatusColors.Green;     // == SeverityGradient green
	static readonly Color RingMid = StatusColors.Yellow;    // rings-only midpoint, for separation
	static readonly Color RingHigh = StatusColors.Red;      // == SeverityGradient red / popup
	static Color RingColor(double pct)
	{
		double p = Math.Clamp(pct, 0, 1);
		return p <= 0.5 ? StatusColors.Lerp(RingLow, RingMid, p / 0.5) : StatusColors.Lerp(RingMid, RingHigh, (p - 0.5) / 0.5);
	}

	void DrawContent(Graphics g)
	{
		// Grab handle: in transparent mode a near-invisible hit strip makes the whole strip grabbable;
		// in ClearType mode the masked background already provides hit-testing, so skip it (an explicit
		// dark strip would survive the alpha mask and show as a black bar).
		if (!_clearType)
			using (var hit = new SolidBrush(Color.FromArgb(1, 0, 0, 0)))
				g.FillRectangle(hit, 0, 0, GrabW, Height);
		var gripColor = _light ? Color.FromArgb(170, 110, 116, 123) : Color.FromArgb(170, 154, 160, 166);
		int gripH = Height - 12;
		using (var grip = new SolidBrush(gripColor))
			g.FillRectangle(grip, 5, (Height - gripH) / 2, 2, gripH);

		DrawAllQuotaSets(g);

		// Grip separators between quota pairs, matching the far-left grab bar (drawn in the gap before each set after the first).
		if (VisibleSetCount > 1)
			using (var sepGrip = new SolidBrush(gripColor))
				for (int i = 1; i < VisibleSetCount; i++)
					g.FillRectangle(sepGrip, GrabW + i * QuotaBlockW - QSep / 2 - 1, (Height - gripH) / 2, 2, gripH);

		// Context rings (busiest-first, nested fullest-outermost): each session is a crisp gradient-coloured
		// arc, no track circle. Capped at RingMaxCount so each ring has room at taskbar size. Sort/cap/
		// overflow live in ContextRingLayout; this just draws the result.
		int originX = GrabW + VisibleSetCount * QuotaBlockW + PadAfterGrab;
		var layout = ContextRingLayout.Build(_sessions, originX, Height, thickness: RingThickness, gap: RingGap, maxRings: RingMaxCount);
		foreach (var ring in layout.Rings)
		{
			if (ring.SweepDegrees <= 0f) continue;
			// Rings use the green->yellow->red palette (endpoints match the popup); error stays warning.
			var arcColor = ring.IsError ? _palette.Warning : RingColor(ring.Pct);
			// Transparent mode draws over an unknown taskbar background; a dark halo under the arc
			// keeps it legible on a light taskbar. ClearType mode draws over the known dark bg.
			if (!_clearType)
				using (var halo = new Pen(Color.FromArgb(190, 0, 0, 0), RingThickness + 2f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
					g.DrawArc(halo, ring.Bounds, -90f, ring.SweepDegrees);
			using (var arcPen = new Pen(arcColor, RingThickness) { StartCap = LineCap.Round, EndCap = LineCap.Round })
				g.DrawArc(arcPen, ring.Bounds, -90f, ring.SweepDegrees);   // 12 o'clock, clockwise
		}

		// Overflow "+K": sessions that did not get a ring, at the top-right of the cluster.
		if (layout.Overflow > 0)
		{
			using var ofont = new Font("Segoe UI", 7.5f, FontStyle.Bold);
			string ktext = "+" + layout.Overflow;
			float kx = originX + RingClusterW + 1f;
			float ky = (Height - Math.Min(Height - 8f, 28f)) / 2f;   // top of the cluster
			if (!_clearType)
				using (var khalo = new SolidBrush(Color.FromArgb(190, 0, 0, 0)))
					for (int dx = -1; dx <= 1; dx++)
						for (int dy = -1; dy <= 1; dy++)
							if (dx != 0 || dy != 0)
								g.DrawString(ktext, ofont, khalo, kx + dx, ky + dy);
			using (var kbrush = new SolidBrush(_palette.Muted))
				g.DrawString(ktext, ofont, kbrush, kx, ky);
		}

		// Switchboard pending badge: an amber dot in the top-right corner, mirroring
		// the tray icon, when there are unanswered questions.
		if (_showBadge && _hasPending)
			using (var dot = new SolidBrush(StatusColors.Amber))
				g.FillEllipse(dot, Width - 10, 2, 8, 8);

		// Claude service-status dot: centered in the context ring cluster. Pulses while an incident
		// is active (Watching); steady once resolved (sticky until acknowledged). A thin dark outline
		// separates it from a ring arc of a similar color.
		if (_claudeDotVisible)
		{
			float dMax = Math.Min(Height - 8f, 28f);
			float penInset = RingThickness / 2f + 1f;
			float od = dMax - 2f * penInset;
			float clusterTop = (Height - dMax) / 2f;
			float ccx = originX + penInset + od / 2f;
			float ccy = clusterTop + penInset + od / 2f;

			float r = 3.3f;
			if (_claudePulse)
				r = 3.3f + 0.9f * (float)Math.Sin(_claudePhase * 2.0 * Math.PI);

			using (var cdot = new SolidBrush(Palette.ForClaudeStatus(_claudeLevel)))
				g.FillEllipse(cdot, ccx - r, ccy - r, r * 2f, r * 2f);
			using (var outline = new Pen(Color.FromArgb(200, 0, 0, 0), 1.25f))
				g.DrawEllipse(outline, ccx - r, ccy - r, r * 2f, r * 2f);
		}
	}

	// Draw the Claude set (if shown) then each touched agy group, side by side. Each set = 2 rows.
	void DrawAllQuotaSets(Graphics g)
	{
		int rowH = Height / 2;
		int set = 0;
		// Antigravity groups first (left-to-right: w/ Claude, then w/ Gemini), matching the popup's top-down order.
		foreach (var group in _agyGroups.OrderBy(AntigravityQuota.GroupSortKey))
		{
			int x = GrabW + set * QuotaBlockW;
			DrawQuotaRow(g, x, rowH / 2, AntigravityQuota.ToUsedWindow(group, "5h"), QuotaPacing.SessionDuration);
			DrawQuotaRow(g, x, rowH + rowH / 2, AntigravityQuota.ToUsedWindow(group, "weekly"), QuotaPacing.WeeklyDuration);
			set++;
		}
		// Claude Code set last (rightmost), matching the popup's bottom position.
		if (QuotaVisible)
		{
			var u = _quota!.Value;
			int x = GrabW + set * QuotaBlockW;
			DrawQuotaRow(g, x, rowH / 2, u.Session, QuotaPacing.SessionDuration);
			DrawQuotaRow(g, x, rowH + rowH / 2, u.Weekly, QuotaPacing.WeeklyDuration);
			set++;
		}
	}

	// One usage row: a 10-segment gradient bar with a thin muted pace bar (filled to the elapsed-time
	// fraction) beneath it, mirroring the popup's ghost pace bar. No text - usage vs pace is read by
	// comparing the two bar lengths.
	void DrawQuotaRow(Graphics g, int xStart, int centerY, QuotaWindow w, TimeSpan duration)
	{
		var fillColor = SeverityGradient.For(w.Percentage / 100.0);
		int barX = xStart;
		int stackTop = centerY - (QSegH + QPaceGap + QPaceH) / 2;

		int segY = stackTop;
		using (var track = new SolidBrush(_palette.Track))
		using (var fill = new SolidBrush(fillColor))
		{
			for (int i = 0; i < QSegCount; i++)
			{
				int segX = barX + i * (QSegW + QSegGap);
				g.FillRectangle(track, segX, segY, QSegW, QSegH);
				double frac = QuotaFormat.SegmentFill(w.Percentage, i, QSegCount);
				if (frac > 0)
					g.FillRectangle(fill, segX, segY, Math.Max(1, (int)Math.Round(QSegW * frac)), QSegH);
			}
		}

		// Pace bar: muted track + muted fill to the elapsed-time fraction (omitted when reset unknown).
		int paceY = stackTop + QSegH + QPaceGap;
		var pace = QuotaPacing.Compute(w, duration, DateTimeOffset.Now);
		using (var paceTrack = new SolidBrush(_palette.Track))
			g.FillRectangle(paceTrack, barX, paceY, QBarW, QPaceH);
		if (pace.ElapsedFraction is double ef)
			using (var paceFill = new SolidBrush(_palette.Muted))
				g.FillRectangle(paceFill, barX, paceY, Math.Max(1, (int)Math.Round(QBarW * ef)), QPaceH);
	}

	// Force background-colored pixels to alpha 1 and everything else to alpha 255 (the monitor's trick:
	// a near-invisible but hit-testable background, with fully opaque content that keeps ClearType edges).
	static void ApplyOpaqueAlphaMask(Bitmap bmp, Color bg)
	{
		var rect = new Rectangle(0, 0, bmp.Width, bmp.Height);
		var data = bmp.LockBits(rect, ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
		try
		{
			int bytes = Math.Abs(data.Stride) * data.Height;
			var buf = new byte[bytes];
			Marshal.Copy(data.Scan0, buf, 0, bytes);
			for (int i = 0; i + 3 < bytes; i += 4)   // 32bppArgb is laid out B,G,R,A in memory
				buf[i + 3] = (buf[i] == bg.B && buf[i + 1] == bg.G && buf[i + 2] == bg.R) ? (byte)1 : (byte)255;
			Marshal.Copy(buf, 0, data.Scan0, bytes);
		}
		finally { bmp.UnlockBits(data); }
	}

	// UpdateLayeredWindow with AC_SRC_ALPHA expects PREMULTIPLIED alpha. GDI+ draws straight alpha
	// into a 32bppArgb bitmap, so premultiply (RGB *= A/255) here. Fully-opaque pixels are unchanged;
	// this is what makes the antialiased text edges composite correctly over the taskbar.
	static void Premultiply(Bitmap bmp)
	{
		var rect = new Rectangle(0, 0, bmp.Width, bmp.Height);
		var data = bmp.LockBits(rect, ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
		try
		{
			int bytes = Math.Abs(data.Stride) * data.Height;
			var buf = new byte[bytes];
			Marshal.Copy(data.Scan0, buf, 0, bytes);
			for (int i = 0; i + 3 < bytes; i += 4)
			{
				byte a = buf[i + 3];
				if (a == 255 || a == 0) continue;     // opaque/transparent pixels need no change
				buf[i] = (byte)(buf[i] * a / 255);     // B
				buf[i + 1] = (byte)(buf[i + 1] * a / 255); // G
				buf[i + 2] = (byte)(buf[i + 2] * a / 255); // R
			}
			Marshal.Copy(buf, 0, data.Scan0, bytes);
		}
		finally { bmp.UnlockBits(data); }
	}

	void SetBitmap(Bitmap bmp)
	{
		IntPtr screenDc = Native.GetDC(IntPtr.Zero);
		IntPtr memDc = Native.CreateCompatibleDC(screenDc);
		IntPtr hBmp = IntPtr.Zero, hOld = IntPtr.Zero;
		try
		{
			hBmp = bmp.GetHbitmap(Color.FromArgb(0));
			hOld = Native.SelectObject(memDc, hBmp);
			var size = new Native.SIZE(bmp.Width, bmp.Height);
			var src = new Native.POINT(0, 0);
			var blend = new Native.BLENDFUNCTION
			{
				BlendOp = Native.AC_SRC_OVER,
				BlendFlags = 0,
				SourceConstantAlpha = 255,
				AlphaFormat = Native.AC_SRC_ALPHA,
			};
			bool ok;
			if (_embedded)
			{
				// Child window: MoveWindow owns position, so pass a NULL pptDst and only update the surface.
				ok = Native.UpdateLayeredWindowNoMove(Handle, screenDc, IntPtr.Zero, ref size, memDc, ref src, 0, ref blend, Native.ULW_ALPHA);
			}
			else
			{
				var dst = new Native.POINT(Left, Top);
				ok = Native.UpdateLayeredWindow(Handle, screenDc, ref dst, ref size, memDc, ref src, 0, ref blend, Native.ULW_ALPHA);
			}
			if (!ok && !_ulwFailing)
			{
				_ulwFailing = true;
				Diagnostic?.Invoke($"UpdateLayeredWindow FAILED err={Marshal.GetLastWin32Error()} embedded={_embedded} handle=0x{Handle:X}");
			}
			else if (ok && _ulwFailing)
			{
				_ulwFailing = false;
				Diagnostic?.Invoke("UpdateLayeredWindow recovered");
			}
		}
		finally
		{
			Native.ReleaseDC(IntPtr.Zero, screenDc);
			if (hBmp != IntPtr.Zero) { Native.SelectObject(memDc, hOld); Native.DeleteObject(hBmp); }
			Native.DeleteDC(memDc);
		}
	}

	protected override void Dispose(bool disposing)
	{
		if (disposing && _winEventHook != IntPtr.Zero)
		{
			Native.UnhookWinEvent(_winEventHook);
			_winEventHook = IntPtr.Zero;
		}
		base.Dispose(disposing);
	}
}
