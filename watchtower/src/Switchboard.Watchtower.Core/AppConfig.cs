using System.Text.Json;

namespace Switchboard.Watchtower.Core;

public sealed class AppConfig
{
	public int PollIntervalSeconds { get; set; } = 60;
	public int ActiveWindowMinutes { get; set; } = 5;
	public int LiveThresholdSeconds { get; set; } = 90;
	public bool ScanWsl { get; set; } = true;
	public bool Autostart { get; set; } = true;
	public int? WidgetX { get; set; } = null;
	public bool? LightThemeOverride { get; set; } = null;
	public bool ShowQuota { get; set; } = true;
	public int QuotaPollMinutes { get; set; } = 5;   // plan-usage poll cadence; 1, 5, 15, or 60

	public static string DefaultPath => Path.Combine(
		Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
		"Switchboard", "Watchtower", "config.json");

	internal static string LegacyPath => Path.Combine(
		Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
		"ClaudeContextWidget", "config.json");

	static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

	/// <summary>Copies a pre-rename config to the new location when the new one is absent. Returns true if a copy happened.</summary>
	public static bool MigrateLegacyConfig(string legacyPath, string newPath)
	{
		if (File.Exists(newPath)) return false;
		if (!File.Exists(legacyPath)) return false;
		Directory.CreateDirectory(Path.GetDirectoryName(newPath)!);
		File.Copy(legacyPath, newPath);
		return true;
	}

	public static AppConfig Load()
	{
		MigrateLegacyConfig(LegacyPath, DefaultPath);
		return LoadFrom(DefaultPath);
	}

	public static AppConfig LoadFrom(string path)
	{
		try
		{
			if (!File.Exists(path)) return new AppConfig();
			var json = File.ReadAllText(path);
			return JsonSerializer.Deserialize<AppConfig>(json, Options) ?? new AppConfig();
		}
		catch
		{
			return new AppConfig();
		}
	}

	public void Save() => SaveTo(DefaultPath);

	public void SaveTo(string path)
	{
		Directory.CreateDirectory(Path.GetDirectoryName(path)!);
		File.WriteAllText(path, JsonSerializer.Serialize(this, Options));
	}
}
