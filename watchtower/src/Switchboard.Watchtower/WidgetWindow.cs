using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.Runtime.InteropServices;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class WidgetWindow : Form
{
	const int DragThreshold = 4;
	const int BarWidth = 6;
	const int BarGap = 3;
	const int GrabW = 12;          // left grab-handle strip; the ONLY drag target
	const int PadAfterGrab = 4;
	const int RightTextRoom = 52;

	// Quota block (CodeZeno-style 5h/7d rows): label + 10-segment bar + "NN% · Xh" text.
	const int QLabelW = 16;
	const int QSegW = 9, QSegGap = 1, QSegCount = 10, QSegH = 8;
	const int QBarW = QSegCount * (QSegW + QSegGap) - QSegGap;          // 49
	const int QBarTextGap = 4;
	const int QTextW = 52;
	const int QSep = 10;                                                // gap before the context bars
	const int QuotaBlockW = QLabelW + QBarW + QBarTextGap + QTextW + QSep;
	const int HeightWithQuota = 44;
	const int HeightContextOnly = 34;

	IReadOnlyList<SessionModel> _sessions = Array.Empty<SessionModel>();
	Palette _palette = new(light: false);
	bool _light;
	bool _clearType = true;   // true = opaque theme bg + ClearType text (monitor style, default); false = true per-pixel-alpha transparency
	QuotaUsage? _quota;       // latest Claude plan usage (5h/7d); null until first successful poll
	bool _showQuota = true;   // user preference; the block also requires _quota to have a value
	bool _showBadge;          // Switchboard ShowBadge preference
	bool _hasPending;         // Switchboard has unanswered questions -> draw the amber badge

	bool QuotaVisible => _showQuota && _quota.HasValue;
	public QuotaUsage? Quota => _quota;

	Point _dragStart;
	bool _pressed;
	bool _dragging;

	IntPtr _taskbar;
	IntPtr _winEventHook;
	bool _embedded;                                // true once reparented as a WS_CHILD of the taskbar
	readonly uint _taskbarCreatedMsg;
	readonly Native.WinEventProc _winEventCb; // kept in a field so the GC cannot collect the callback

	public event Action<int>? PositionChanged;    // new screen X persisted by the host
	public Action<string>? Diagnostic;             // optional sink for embed/lifecycle diagnostics

	public bool IsEmbedded => _embedded;

	[System.ComponentModel.DesignerSerializationVisibility(System.ComponentModel.DesignerSerializationVisibility.Hidden)]
	public int? PreferredX { get; set; }          // screen X from config; null = auto (left of tray)

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

		if (_winEventHook == IntPtr.Zero && _taskbar != IntPtr.Zero)
		{
			uint thread = Native.GetWindowThreadProcessId(_taskbar, out _);
			_winEventHook = Native.SetWinEventHook(
				Native.EVENT_OBJECT_LOCATIONCHANGE, Native.EVENT_OBJECT_LOCATIONCHANGE,
				IntPtr.Zero, _winEventCb, 0, thread, Native.WINEVENT_OUTOFCONTEXT);
		}
		PositionOverTaskbar();
	}

	// Reparent this window into the taskbar following the exact sequence the CodeZeno monitor uses:
	// preserve WS_EX_LAYERED and add tool-window/no-activate, flip WS_POPUP -> WS_CHILD|WS_CLIPSIBLINGS,
	// refresh the frame, then SetParent last. Style is changed via SetWindowLong AFTER handle creation
	// (NOT CreateParams) because a top-level WinForms Form ignores WS_CHILD in CreateParams.
	void TryEmbed()
	{
		if (_embedded && Native.GetParent(Handle) == _taskbar) return;

		uint ex = (uint)Native.GetWindowLongPtr(Handle, Native.GWL_EXSTYLE);
		Native.SetWindowLongPtr(Handle, Native.GWL_EXSTYLE,
			(nint)(ex | (uint)Native.WS_EX_TOOLWINDOW | (uint)Native.WS_EX_NOACTIVATE));

		uint style = (uint)Native.GetWindowLongPtr(Handle, Native.GWL_STYLE);
		uint newStyle = (style & ~unchecked((uint)Native.WS_POPUP)) | (uint)Native.WS_CHILD | (uint)Native.WS_CLIPSIBLINGS;
		Native.SetWindowLongPtr(Handle, Native.GWL_STYLE, (nint)newStyle);

		// Force the frame change to take effect before reparenting (the Rust app gets away without this
		// because it is a raw window; a WinForms Form needs the explicit SWP_FRAMECHANGED refresh).
		Native.SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0,
			Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOZORDER | Native.SWP_FRAMECHANGED | Native.SWP_NOACTIVATE);

		Native.SetParent(Handle, _taskbar);
		_embedded = Native.GetParent(Handle) == _taskbar;
		if (_embedded) TopMost = false; // meaningless and counter-productive for a child window

		Diagnostic?.Invoke($"embed attempt: taskbar={_taskbar:X} embedded={_embedded} style=0x{newStyle:X8}");
	}

	// The window's true on-screen rectangle. Form.Bounds is parent-relative (and stale) once embedded,
	// so hover hit-testing and the detail panel must use this instead.
	public Rectangle ScreenBounds =>
		IsHandleCreated && Native.GetWindowRect(Handle, out var r)
			? Rectangle.FromLTRB(r.Left, r.Top, r.Right, r.Bottom)
			: Bounds;

	int CurrentScreenLeft() => IsHandleCreated && Native.GetWindowRect(Handle, out var r) ? r.Left : Left;

	public void KeepOnTop()
	{
		if (_pressed) { if (!_embedded) Raise(); return; }

		// If we were embedded and lost our parent (explorer restart / taskbar recreated), re-attach.
		if (_embedded && Native.GetParent(Handle) != _taskbar)
		{
			Diagnostic?.Invoke("embedded child detached from taskbar; re-attaching");
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
		try { if (IsHandleCreated) BeginInvoke(new Action(PositionOverTaskbar)); }
		catch { /* shutting down */ }
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

	// Show/clear the Switchboard pending badge (an amber dot), mirroring the tray icon.
	// No size change, so this only repaints.
	public void SetPending(bool showBadge, bool hasPending)
	{
		if (_showBadge == showBadge && _hasPending == hasPending) return;
		_showBadge = showBadge;
		_hasPending = hasPending;
		Render();
	}

	// Re-render only (no reposition): the countdown text is recomputed from the stored reset times at
	// paint time, so the host can tick this faster than the poll without refetching.
	public void RefreshQuotaCountdown()
	{
		if (QuotaVisible) Render();
	}

	void RecomputeSize()
	{
		int bars = Math.Max(1, _sessions.Count) * (BarWidth + BarGap);
		int quotaW = QuotaVisible ? QuotaBlockW : 0;
		Width = Math.Max(72, GrabW + quotaW + PadAfterGrab + bars + RightTextRoom);
		Height = QuotaVisible ? HeightWithQuota : HeightContextOnly;
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
				int desiredScreenX = CurrentScreenLeft() + (e.X - _dragStart.X);
				PreferredX = TaskbarPlacement.ClampScreenX(desiredScreenX, tb, trayLeft, Width);
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

		if (QuotaVisible) DrawQuota(g);

		// Equalizer bars (busiest-first); error sessions full-height in the warning color.
		bool anyError = false;
		double max = 0;
		foreach (var s in _sessions)
		{
			anyError |= s.IsError;
			if (!s.IsError && s.Pct >= max) max = s.Pct;
		}

		int x = GrabW + (QuotaVisible ? QuotaBlockW : 0) + PadAfterGrab;
		int baseY = Height - 6;
		int maxBarH = Math.Min(Height - 12, 24);   // keep the equalizer from dominating the taller quota layout
		foreach (var s in _sessions)
		{
			var color = s.IsError ? _palette.Warning : SeverityGradient.For(s.Pct);
			int h = s.IsError ? maxBarH : Math.Max(2, (int)(maxBarH * Math.Clamp(s.Pct, 0, 1)));
			using var brush = new SolidBrush(color);
			g.FillRectangle(brush, x, baseY - h, BarWidth, h);
			x += BarWidth + BarGap;
		}

		// Max % label.
		var labelColor = anyError ? _palette.Warning : SeverityGradient.For(max);
		// show the % only when at least one session is >= 50% (or on error); otherwise just the bars
		var text = anyError ? "!" : max >= 0.50 ? $"{(int)Math.Round(max * 100)}%" : "";
		using var font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
		var size = g.MeasureString(text, font);
		float tx = Width - size.Width - 6;
		float ty = (Height - size.Height) / 2;
		// Transparent mode draws over an unknown taskbar background, so a dark halo keeps the number
		// readable. ClearType mode draws over the known opaque theme bg and needs no halo.
		if (!_clearType)
			using (var halo = new SolidBrush(Color.FromArgb(190, 0, 0, 0)))
			{
				for (int dx = -1; dx <= 1; dx++)
					for (int dy = -1; dy <= 1; dy++)
						if (dx != 0 || dy != 0)
							g.DrawString(text, font, halo, tx + dx, ty + dy);
			}
		using var textBrush = new SolidBrush(labelColor);
		g.DrawString(text, font, textBrush, tx, ty);

		// Switchboard pending badge: an amber dot in the top-right corner, mirroring
		// the tray icon, when there are unanswered questions.
		if (_showBadge && _hasPending)
			using (var dot = new SolidBrush(Color.FromArgb(210, 153, 34)))
				g.FillEllipse(dot, Width - 10, 2, 8, 8);
	}

	// Two CodeZeno-style usage rows (5h on top, 7d below), left of the context equalizer.
	void DrawQuota(Graphics g)
	{
		var u = _quota!.Value;
		int rowH = Height / 2;
		DrawQuotaRow(g, GrabW, rowH / 2, "5h", u.Session);
		DrawQuotaRow(g, GrabW, rowH + rowH / 2, "7d", u.Weekly);
	}

	void DrawQuotaRow(Graphics g, int xStart, int centerY, string label, QuotaWindow w)
	{
		var fillColor = SeverityGradient.For(w.Percentage / 100.0);   // bar + "NN% · Xh" label share this gradient colour
		using var font = new Font("Segoe UI", 7.5f, FontStyle.Bold);

		var labelSize = g.MeasureString(label, font);
		using (var muted = new SolidBrush(_palette.Muted))
			g.DrawString(label, font, muted, xStart, centerY - labelSize.Height / 2);

		int barX = xStart + QLabelW;
		int segY = centerY - QSegH / 2;
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

		string text = QuotaFormat.Line(w.Percentage, w.ResetsAt, DateTimeOffset.Now);
		var textSize = g.MeasureString(text, font);
		using (var vb = new SolidBrush(fillColor))
			g.DrawString(text, font, vb, barX + QBarW + QBarTextGap, centerY - textSize.Height / 2);
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
			if (_embedded)
			{
				// Child window: MoveWindow owns position, so pass a NULL pptDst and only update the surface.
				Native.UpdateLayeredWindowNoMove(Handle, screenDc, IntPtr.Zero, ref size, memDc, ref src, 0, ref blend, Native.ULW_ALPHA);
			}
			else
			{
				var dst = new Native.POINT(Left, Top);
				Native.UpdateLayeredWindow(Handle, screenDc, ref dst, ref size, memDc, ref src, 0, ref blend, Native.ULW_ALPHA);
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
