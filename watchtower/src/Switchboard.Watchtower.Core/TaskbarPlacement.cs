using System.Drawing;

namespace Switchboard.Watchtower.Core;

/// <summary>Where to place the widget along the taskbar.</summary>
/// <remarks>
/// <see cref="X"/>/<see cref="Y"/> are SCREEN coordinates when the widget is a top-level
/// overlay, and PARENT-RELATIVE (to the taskbar's client origin) when it is embedded as a
/// <c>WS_CHILD</c> of <c>Shell_TrayWnd</c>. The only difference between the two is subtracting
/// the taskbar's screen origin, which is the conversion that has to be exactly right for an
/// embedded child to land on-screen instead of off in the corner.
/// </remarks>
public readonly record struct WidgetPlacement(int X, int Y);

public static class TaskbarPlacement
{
	const int TrayGap = 8;        // gap between the widget and the tray cluster
	const int NoTrayInset = 160;  // fallback inset from the taskbar right edge when the tray is unknown

	/// <summary>
	/// Compute where to put the widget. Pass <paramref name="embedded"/> = true to get
	/// parent-relative coordinates for a taskbar child window; false for a screen-coordinate overlay.
	/// </summary>
	public static WidgetPlacement Compute(
		Rectangle taskbar, int? trayLeft, int widgetWidth, int widgetHeight, int? preferredScreenX, bool embedded)
	{
		int y = taskbar.Top + (taskbar.Height - widgetHeight) / 2;
		int x;
		if (preferredScreenX is int px)
			x = ClampScreenX(px, taskbar, trayLeft, widgetWidth);
		else if (trayLeft is int tl)
			x = tl - widgetWidth - TrayGap;
		else
			x = taskbar.Right - widgetWidth - NoTrayInset;
		// A WS_CHILD of the taskbar takes coordinates relative to the parent's client origin, so
		// convert the screen anchor by subtracting the taskbar origin. The overlay keeps screen coords.
		return embedded ? new(x - taskbar.Left, y - taskbar.Top) : new(x, y);
	}

	/// <summary>
	/// Clamp a desired screen X so the widget stays within the taskbar and left of the tray cluster
	/// (so it can't be dragged over the clock). Falls back to the taskbar right edge when the tray is unknown.
	/// </summary>
	public static int ClampScreenX(int desiredScreenX, Rectangle taskbar, int? trayLeft, int widgetWidth)
	{
		int rightLimit = (trayLeft ?? taskbar.Right) - widgetWidth;
		return Math.Clamp(desiredScreenX, taskbar.Left, Math.Max(taskbar.Left, rightLimit));
	}
}
