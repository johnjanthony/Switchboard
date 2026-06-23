using Switchboard.Watchtower.Core;
using Xunit;

public class AppConfigTests
{
	[Fact]
	public void Defaults_match_spec()
	{
		var c = new AppConfig();
		Assert.Equal(60, c.PollIntervalSeconds);
		Assert.Equal(5, c.ActiveWindowMinutes);
		Assert.Equal(90, c.LiveThresholdSeconds);
		Assert.True(c.ScanWsl);
		Assert.True(c.Autostart);
		Assert.Null(c.WidgetX);
	}

	[Fact]
	public void LoadFrom_missing_file_returns_defaults()
	{
		var path = Path.Combine(Path.GetTempPath(), "cccfg-missing-" + Guid.NewGuid().ToString("N") + ".json");
		var c = AppConfig.LoadFrom(path);
		Assert.Equal(60, c.PollIntervalSeconds);
	}

	[Fact]
	public void SaveTo_then_LoadFrom_round_trips()
	{
		var path = Path.Combine(Path.GetTempPath(), "cccfg-" + Guid.NewGuid().ToString("N") + ".json");
		try
		{
			var c = new AppConfig { PollIntervalSeconds = 30, WidgetX = 1234, Autostart = false };
			c.SaveTo(path);
			var loaded = AppConfig.LoadFrom(path);
			Assert.Equal(30, loaded.PollIntervalSeconds);
			Assert.Equal(1234, loaded.WidgetX);
			Assert.False(loaded.Autostart);
		}
		finally { if (File.Exists(path)) File.Delete(path); }
	}

	[Fact]
	public void Switchboard_defaults_match_spec()
	{
		var c = new AppConfig();
		Assert.NotNull(c.Switchboard);
		Assert.False(c.Switchboard.Enabled);
		Assert.Equal("http://localhost:9876/stats", c.Switchboard.StatsUrl);
		Assert.Equal("http://localhost:9876/dashboard", c.Switchboard.DashboardUrl);
		Assert.False(c.Switchboard.ShowBadge);
	}

	[Fact]
	public void Switchboard_block_round_trips()
	{
		var path = Path.Combine(Path.GetTempPath(), "cccfg-sb-" + Guid.NewGuid().ToString("N") + ".json");
		try
		{
			var c = new AppConfig();
			c.Switchboard.Enabled = true;
			c.Switchboard.StatsUrl = "http://192.168.1.5:9876/stats";
			c.Switchboard.DashboardUrl = "http://192.168.1.5:9876/dashboard";
			c.Switchboard.ShowBadge = true;
			c.SaveTo(path);
			var loaded = AppConfig.LoadFrom(path);
			Assert.True(loaded.Switchboard.Enabled);
			Assert.Equal("http://192.168.1.5:9876/stats", loaded.Switchboard.StatsUrl);
			Assert.Equal("http://192.168.1.5:9876/dashboard", loaded.Switchboard.DashboardUrl);
			Assert.True(loaded.Switchboard.ShowBadge);
		}
		finally { if (File.Exists(path)) File.Delete(path); }
	}

	[Fact]
	public void ClaudeStatus_config_has_sensible_defaults()
	{
		var cfg = new AppConfig();
		Assert.NotNull(cfg.ClaudeStatus);
		Assert.Equal("https://status.claude.com/api/v2/summary.json", cfg.ClaudeStatus.SummaryUrl);
		Assert.Equal(60, cfg.ClaudeStatus.WatchIntervalSeconds);
		Assert.Equal(180, cfg.ClaudeStatus.MaxWatchMinutes);
	}

	[Fact]
	public void ClaudeStatus_config_absent_block_loads_defaults()
	{
		var path = Path.Combine(Path.GetTempPath(), "wt-claudestatus-" + Guid.NewGuid().ToString("N") + ".json");
		try
		{
			File.WriteAllText(path, "{\"PollIntervalSeconds\":60}");
			var cfg = AppConfig.LoadFrom(path);
			Assert.Equal("https://status.claude.com/api/v2/summary.json", cfg.ClaudeStatus.SummaryUrl);
			Assert.Equal(60, cfg.ClaudeStatus.WatchIntervalSeconds);
		}
		finally { if (File.Exists(path)) File.Delete(path); }
	}
}
