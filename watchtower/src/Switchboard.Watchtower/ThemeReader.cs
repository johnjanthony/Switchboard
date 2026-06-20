using Microsoft.Win32;

namespace Switchboard.Watchtower;

internal static class ThemeReader
{
	// True when the taskbar uses the light theme. Defaults to dark (false) if the value is missing.
	public static bool IsLightTaskbar()
	{
		try
		{
			using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
			var v = key?.GetValue("SystemUsesLightTheme");
			return v is int i && i != 0;
		}
		catch { return false; }
	}
}
