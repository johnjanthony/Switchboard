using System.Drawing;

namespace Switchboard.Watchtower;

internal static class TaskbarLocator
{
	public static bool TryGetTaskbarRect(out Rectangle rect)
	{
		rect = Rectangle.Empty;
		var tray = Native.FindWindow("Shell_TrayWnd", null);
		if (tray == IntPtr.Zero) return false;
		if (!Native.GetWindowRect(tray, out var r)) return false;
		rect = FromRect(r);
		return true;
	}

	// The clock/tray cluster, used to position the widget just to its left.
	public static bool TryGetTrayRect(out Rectangle rect)
	{
		rect = Rectangle.Empty;
		var tray = Native.FindWindow("Shell_TrayWnd", null);
		if (tray == IntPtr.Zero) return false;
		var notify = Native.FindWindowEx(tray, IntPtr.Zero, "TrayNotifyWnd", null);
		if (notify == IntPtr.Zero) return false;
		if (!Native.GetWindowRect(notify, out var r)) return false;
		rect = FromRect(r);
		return true;
	}

	static Rectangle FromRect(Native.RECT r) => new(r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top);
}
