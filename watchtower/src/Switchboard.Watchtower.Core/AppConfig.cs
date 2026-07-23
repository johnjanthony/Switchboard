using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;

namespace Switchboard.Watchtower.Core;

public sealed class AppConfig
{
	[JsonIgnore] public bool LoadDegraded { get; private set; }

	const int MaxReadRetries = 3;
	const int ReadRetryDelayMs = 100;

	public int PollIntervalSeconds { get; set; } = 60;
	public int ActiveWindowMinutes { get; set; } = 5;
	public int LiveThresholdSeconds { get; set; } = 90;
	public bool ScanWsl { get; set; } = true;
	public bool ScanAntigravity { get; set; } = true;
	public bool Autostart { get; set; } = true;
	public int? WidgetX { get; set; } = null;
	public bool? LightThemeOverride { get; set; } = null;
	public bool ShowQuota { get; set; } = true;
	public int QuotaPollMinutes { get; set; } = 5;   // plan-usage poll cadence; 1, 5, 15, or 60
	public bool DailyAnchorEnabled { get; set; } = true;
	public string DailyAnchorTime { get; set; } = "07:00";   // local "HH:mm"; the daily session-anchor fire time
	public bool PollAntigravityQuota { get; set; } = true;
	public int AntigravityQuotaPollIntervalSeconds { get; set; } = 60;

	// Parsed anchor time; falls back to 07:00 on a malformed value WITHOUT flagging degraded,
	// so a bad time string can never block config saves (LoadDegraded gates SaveTo).
	[JsonIgnore]
	public TimeOnly DailyAnchorTimeOfDay =>
		TimeOnly.TryParseExact(DailyAnchorTime, "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var t)
			? t : new TimeOnly(7, 0);

	public SwitchboardConfig Switchboard { get; set; } = new();
	public ClaudeStatusConfig ClaudeStatus { get; set; } = new();

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
		if (!File.Exists(path)) return new AppConfig();               // absent -> defaults, savable
		for (int attempt = 0; attempt <= MaxReadRetries; attempt++)
		{
			try
			{
				var parsed = JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(path), Options);
				return parsed ?? new AppConfig { LoadDegraded = true };  // null parse == do not adopt defaults over it
			}
			catch (IOException) when (attempt < MaxReadRetries) { Thread.Sleep(ReadRetryDelayMs); }
			catch { return new AppConfig { LoadDegraded = true }; }      // present but unreadable/unparseable
		}
		return new AppConfig { LoadDegraded = true };                    // IO retries exhausted
	}

	public bool Save() => SaveTo(DefaultPath);

	public bool SaveTo(string path)
	{
		if (LoadDegraded) return false;                              // never clobber a config we could not read
		Directory.CreateDirectory(Path.GetDirectoryName(path)!);
		var tmp = path + ".tmp";
		File.WriteAllText(tmp, JsonSerializer.Serialize(this, Options));
		File.Move(tmp, path, overwrite: true);                       // atomic replace; handles first-write
		return true;
	}
}

/// <summary>Watchtower's Switchboard integration: the stats line, dashboard launcher, and optional tray badge.</summary>
public sealed class SwitchboardConfig
{
	/// <summary>Gates the entire Switchboard UI (stats line, launcher, badge). When false the block is hidden.</summary>
	public bool Enabled { get; set; }

	/// <summary>Localhost stats endpoint the widget polls. Point at the Windows host IP for a WSL-hosted server.</summary>
	public string StatsUrl { get; set; } = "http://localhost:9876/stats";

	/// <summary>Localhost ingest endpoint Watchtower POSTs its rings + quota snapshot to. Point at the Windows host IP for a WSL-hosted server.</summary>
	public string SnapshotUrl { get; set; } = "http://localhost:9876/widget-snapshot";

	/// <summary>Operator dashboard URL the launcher opens; may have #conv=&lt;id&gt; appended to deep-link.</summary>
	public string DashboardUrl { get; set; } = "http://localhost:9876/dashboard";

	/// <summary>When true and pending_count is greater than zero, the tray icon shows a pending badge.</summary>
	public bool ShowBadge { get; set; }

	/// <summary>How often (seconds) to poll /stats. Decoupled from the 60s session-scan cadence so the pending badge updates near-instantly. Floored at 2s.</summary>
	public int PollSeconds { get; set; } = 4;
}

/// <summary>Watchtower's Claude service-status indicator: now a thin client of the server's
/// /widget-status (the watch loop + fetch live on the server). StatusUrl is the server endpoint;
/// PollSeconds is how often the dot re-syncs from the server.</summary>
public sealed class ClaudeStatusConfig
{
	/// <summary>The server's status endpoint Watchtower GETs (view) and POSTs (check/stop) against.</summary>
	public string StatusUrl { get; set; } = "http://localhost:9876/widget-status";

	/// <summary>How often (seconds) to re-sync the dot from the server view. Floored at 2s by the host.</summary>
	public int PollSeconds { get; set; } = 5;
}
