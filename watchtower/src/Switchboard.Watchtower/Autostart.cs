using Microsoft.Win32;

namespace Switchboard.Watchtower;

internal static class Autostart
{
	const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
	const string ValueName = "Switchboard Watchtower";
	const string LegacyValueName = "ClaudeContextWidget";

	public static void Apply(bool enabled, string exePath)
	{
		using var key = Registry.CurrentUser.OpenSubKey(RunKey, writable: true);
		if (key is null) return;
		// Remove the pre-rename entry so we never double-launch after an upgrade.
		key.DeleteValue(LegacyValueName, throwOnMissingValue: false);
		if (enabled) key.SetValue(ValueName, $"\"{exePath}\"");
		else key.DeleteValue(ValueName, throwOnMissingValue: false);
	}
}
