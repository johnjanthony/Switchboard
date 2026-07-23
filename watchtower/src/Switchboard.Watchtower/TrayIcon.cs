using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class TrayIcon : IDisposable
{
	static readonly int[] PollMinuteChoices = { 1, 5, 15, 60 };

	readonly NotifyIcon _icon;
	readonly ToolStripMenuItem _autostartItem;
	readonly ToolStripMenuItem _clearTypeItem;
	readonly ToolStripMenuItem _showQuotaItem;
	readonly ToolStripMenuItem _claudeStatusItem;
	readonly ToolStripMenuItem _wakeTimeItem;
	readonly List<ToolStripMenuItem> _intervalItems = new();
	IntPtr _hicon;
	Icon? _ownedIcon;
	bool _showBadge;
	bool _hasPending;
	double _lastPct;
	bool _lastAnyError;
	Severity _lastSeverity = Severity.Green;
	bool _lastLight;

	public event Action? RefreshRequested;
	public event Action? ClaudeStatusActionRequested;
	public event Action? WakeTimeRequested;
	public event Action<bool>? AutostartToggled;
	public event Action<bool>? RenderModeToggled;    // true = opaque ClearType, false = true transparency
	public event Action<bool>? QuotaShowToggled;     // show/hide the plan-usage block
	public event Action<int>? QuotaIntervalChanged;  // new poll cadence in minutes
	public event Action? QuitRequested;
	public event Action? OpenDashboardRequested;

	public TrayIcon(bool autostartOn, bool showQuota, int quotaPollMinutes)
	{
		var menu = new ContextMenuStrip();
		menu.Items.Add("Refresh now", null, (_, _) => RefreshRequested?.Invoke());
		_claudeStatusItem = new ToolStripMenuItem("Check Claude status", null, (_, _) => ClaudeStatusActionRequested?.Invoke());
		menu.Items.Add(_claudeStatusItem);
		_autostartItem = new ToolStripMenuItem("Start with Windows", null, (_, _) =>
		{
			_autostartItem!.Checked = !_autostartItem.Checked;
			AutostartToggled?.Invoke(_autostartItem.Checked);
		})
		{ Checked = autostartOn, CheckOnClick = false };
		menu.Items.Add(_autostartItem);
		_clearTypeItem = new ToolStripMenuItem("Crisp text (ClearType)", null, (_, _) =>
		{
			_clearTypeItem!.Checked = !_clearTypeItem.Checked;
			RenderModeToggled?.Invoke(_clearTypeItem.Checked);
		})
		{ Checked = true, CheckOnClick = false };
		menu.Items.Add(_clearTypeItem);

		menu.Items.Add(new ToolStripSeparator());
		_showQuotaItem = new ToolStripMenuItem("Show plan usage", null, (_, _) =>
		{
			_showQuotaItem!.Checked = !_showQuotaItem.Checked;
			QuotaShowToggled?.Invoke(_showQuotaItem.Checked);
		})
		{ Checked = showQuota, CheckOnClick = false };
		menu.Items.Add(_showQuotaItem);

		var intervalMenu = new ToolStripMenuItem("Usage poll interval");
		foreach (int minutes in PollMinuteChoices)
		{
			int choice = minutes;
			var item = new ToolStripMenuItem(IntervalLabel(choice), null, (_, _) => SelectInterval(choice))
			{ Checked = choice == quotaPollMinutes, CheckOnClick = false };
			_intervalItems.Add(item);
			intervalMenu.DropDownItems.Add(item);
		}
		menu.Items.Add(intervalMenu);

		_wakeTimeItem = new ToolStripMenuItem("Daily wake time", null, (_, _) => WakeTimeRequested?.Invoke());
		menu.Items.Add(_wakeTimeItem);

		menu.Items.Add(new ToolStripSeparator());
		menu.Items.Add("Open Switchboard dashboard", null, (_, _) => OpenDashboardRequested?.Invoke());

		menu.Items.Add(new ToolStripSeparator());
		menu.Items.Add("Quit", null, (_, _) => QuitRequested?.Invoke());

		_icon = new NotifyIcon
		{
			Icon = SystemIcons.Application,   // placeholder, replaced immediately below
			Visible = true,
			Text = "Claude Context Widget",
			ContextMenuStrip = menu,
		};
		SetGauge(0, anyError: false, Severity.Green, light: false);
	}

	public void SetWakeTime(bool enabled, TimeOnly timeOfDay)
	{
		string formatted = DateTime.Today.Add(timeOfDay.ToTimeSpan()).ToString("h:mm tt");
		_wakeTimeItem.Text = enabled
			? $"Daily wake time ({formatted})"
			: "Daily wake time (Off)";
	}

	// Render the tray icon as a ring gauge of the busiest session's context fullness, like the VS Code
	// Claude Code context indicator: a muted track ring with a severity-colored arc filling clockwise.
	public void SetGauge(double pct, bool anyError, Severity severity, bool light)
	{
		_lastPct = pct;
		_lastAnyError = anyError;
		_lastSeverity = severity;
		_lastLight = light;
		var palette = new Palette(light);
		Color arc = anyError ? palette.Warning : Palette.ForSeverity(severity);
		using var bmp = RenderGauge(pct, arc, palette.Track, anyError, _showBadge && _hasPending);

		IntPtr hicon = bmp.GetHicon();
		var icon = Icon.FromHandle(hicon);
		_icon.Icon = icon;
		_icon.Text = anyError
			? "Claude Context Widget - attention needed"
			: $"Claude Context Widget - {(int)Math.Round(Math.Clamp(pct, 0, 1) * 100)}%";

		// Icon.FromHandle does not own the HICON, so free the previous icon + handle ourselves.
		var oldIcon = _ownedIcon;
		var oldHicon = _hicon;
		_ownedIcon = icon;
		_hicon = hicon;
		oldIcon?.Dispose();
		if (oldHicon != IntPtr.Zero) Native.DestroyIcon(oldHicon);
	}

	static Bitmap RenderGauge(double pct, Color arc, Color track, bool anyError, bool badge)
	{
		var bmp = new Bitmap(32, 32, PixelFormat.Format32bppArgb);
		using var g = Graphics.FromImage(bmp);
		g.SmoothingMode = SmoothingMode.AntiAlias;
		g.Clear(Color.Transparent);

		const float thickness = 5f;
		float inset = thickness / 2f + 1f;
		var ring = new RectangleF(inset, inset, 32 - 2 * inset, 32 - 2 * inset);

		using (var trackPen = new Pen(track, thickness))
			g.DrawEllipse(trackPen, ring);

		float sweep = anyError ? 360f : (float)(360.0 * Math.Clamp(pct, 0, 1));
		if (sweep > 0f)
			using (var arcPen = new Pen(arc, thickness) { StartCap = LineCap.Round, EndCap = LineCap.Round })
				g.DrawArc(arcPen, ring, -90f, sweep);   // start at 12 o'clock, fill clockwise

		// Pending badge: a small amber dot in the top-right corner when there are unanswered questions.
		if (badge)
		{
			using var dotBrush = new SolidBrush(StatusColors.Amber);
			g.FillEllipse(dotBrush, 22, 2, 8, 8);
		}

		return bmp;
	}

	static string IntervalLabel(int minutes) => minutes == 60 ? "1 hour" : minutes == 1 ? "1 minute" : $"{minutes} minutes";

	void SelectInterval(int minutes)
	{
		foreach (var item in _intervalItems)
			item.Checked = item.Text == IntervalLabel(minutes);
		QuotaIntervalChanged?.Invoke(minutes);
	}

	// Configure the optional pending badge. The dot is overlaid on the gauge when showBadge && pending > 0.
	public void SetPending(bool showBadge, bool hasPending)
	{
		_showBadge = showBadge;
		_hasPending = hasPending;
		SetGauge(_lastPct, _lastAnyError, _lastSeverity, _lastLight);
	}

	// Mirror the popup button's contextual label.
	public void SetClaudeStatusButton(ClaudeStatusButton button)
	{
		_claudeStatusItem.Text = button switch
		{
			ClaudeStatusButton.StopWatching => "Stop watching Claude status",
			ClaudeStatusButton.Clear => "Clear Claude status",
			_ => "Check Claude status",
		};
	}

	public void Dispose()
	{
		_icon.Visible = false;
		_icon.Dispose();
		_ownedIcon?.Dispose();
		if (_hicon != IntPtr.Zero) Native.DestroyIcon(_hicon);
	}
}
