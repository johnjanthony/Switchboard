using System.Runtime.InteropServices;

namespace Switchboard.Watchtower;

internal static class Native
{
	[StructLayout(LayoutKind.Sequential)]
	public struct RECT { public int Left, Top, Right, Bottom; }

	[DllImport("user32.dll", SetLastError = true)]
	public static extern IntPtr FindWindow(string? lpClassName, string? lpWindowName);

	[DllImport("user32.dll", SetLastError = true)]
	public static extern IntPtr FindWindowEx(IntPtr parent, IntPtr childAfter, string? lpszClass, string? lpszWindow);

	[DllImport("user32.dll")]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

	public const int WS_EX_TOOLWINDOW = 0x00000080;
	public const int WS_EX_NOACTIVATE = 0x08000000;
	public const uint EVENT_OBJECT_LOCATIONCHANGE = 0x800B;
	public const uint WINEVENT_OUTOFCONTEXT = 0x0000;

	public delegate void WinEventProc(IntPtr hWinEventHook, uint eventType, IntPtr hwnd, int idObject, int idChild, uint idEventThread, uint dwmsEventTime);

	[DllImport("user32.dll", SetLastError = true)]
	public static extern IntPtr SetWinEventHook(uint eventMin, uint eventMax, IntPtr hmodWinEventProc, WinEventProc lpfnWinEventProc, uint idProcess, uint idThread, uint dwFlags);

	[DllImport("user32.dll")]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool UnhookWinEvent(IntPtr hWinEventHook);

	[DllImport("user32.dll", SetLastError = true)]
	public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

	[DllImport("user32.dll", SetLastError = true)]
	public static extern uint RegisterWindowMessage(string lpString);

	public static readonly IntPtr HWND_TOPMOST = new(-1);
	public const uint SWP_NOSIZE = 0x0001;
	public const uint SWP_NOMOVE = 0x0002;
	public const uint SWP_NOACTIVATE = 0x0010;

	[DllImport("user32.dll", SetLastError = true)]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

	public const int WS_EX_LAYERED = 0x00080000;
	public const byte AC_SRC_OVER = 0x00;
	public const byte AC_SRC_ALPHA = 0x01;
	public const int ULW_ALPHA = 0x02;

	[StructLayout(LayoutKind.Sequential)]
	public struct POINT { public int X; public int Y; public POINT(int x, int y) { X = x; Y = y; } }

	[StructLayout(LayoutKind.Sequential)]
	public struct SIZE { public int Cx; public int Cy; public SIZE(int cx, int cy) { Cx = cx; Cy = cy; } }

	[StructLayout(LayoutKind.Sequential, Pack = 1)]
	public struct BLENDFUNCTION { public byte BlendOp; public byte BlendFlags; public byte SourceConstantAlpha; public byte AlphaFormat; }

	[DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hWnd);
	[DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
	[DllImport("gdi32.dll")] public static extern IntPtr CreateCompatibleDC(IntPtr hDC);
	[DllImport("gdi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool DeleteDC(IntPtr hdc);
	[DllImport("gdi32.dll")] public static extern IntPtr SelectObject(IntPtr hdc, IntPtr h);
	[DllImport("gdi32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool DeleteObject(IntPtr ho);

	[DllImport("user32.dll", SetLastError = true)]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool UpdateLayeredWindow(IntPtr hwnd, IntPtr hdcDst, ref POINT pptDst, ref SIZE psize, IntPtr hdcSrc, ref POINT pptSrc, int crKey, ref BLENDFUNCTION pblend, int dwFlags);

	// Same entry point, but with a NULL pptDst so the call only updates the surface contents and never
	// moves the window. Used on the embedded (child) path, where MoveWindow owns position instead.
	[DllImport("user32.dll", EntryPoint = "UpdateLayeredWindow", SetLastError = true)]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool UpdateLayeredWindowNoMove(IntPtr hwnd, IntPtr hdcDst, IntPtr pptDst, ref SIZE psize, IntPtr hdcSrc, ref POINT pptSrc, int crKey, ref BLENDFUNCTION pblend, int dwFlags);

	// --- Taskbar child-window embedding ---

	public const int GWL_STYLE = -16;
	public const int GWL_EXSTYLE = -20;
	public const int WS_CHILD = 0x40000000;
	public const int WS_POPUP = unchecked((int)0x80000000);
	public const int WS_CLIPSIBLINGS = 0x04000000;
	public const uint SWP_NOZORDER = 0x0004;
	public const uint SWP_FRAMECHANGED = 0x0020;

	[DllImport("user32.dll", SetLastError = true)]
	public static extern IntPtr SetParent(IntPtr hWndChild, IntPtr hWndNewParent);

	[DllImport("user32.dll", SetLastError = true)]
	public static extern IntPtr GetParent(IntPtr hWnd);

	[DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
	public static extern nint GetWindowLongPtr(IntPtr hWnd, int nIndex);

	[DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
	public static extern nint SetWindowLongPtr(IntPtr hWnd, int nIndex, nint dwNewLong);

	[DllImport("user32.dll", SetLastError = true)]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, [MarshalAs(UnmanagedType.Bool)] bool bRepaint);

	public static readonly IntPtr HWND_TOP = IntPtr.Zero;
	public const uint SWP_SHOWWINDOW = 0x0040;
	public const int SW_SHOWNA = 8;                 // show without activating
	public const int WS_VISIBLE = 0x10000000;

	[DllImport("user32.dll")]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool IsWindowVisible(IntPtr hWnd);

	[DllImport("user32.dll", SetLastError = true)]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

	// Frees an HICON created via Bitmap.GetHicon(); Icon.FromHandle does not own the handle.
	[DllImport("user32.dll", SetLastError = true)]
	[return: MarshalAs(UnmanagedType.Bool)]
	public static extern bool DestroyIcon(IntPtr hIcon);
}
