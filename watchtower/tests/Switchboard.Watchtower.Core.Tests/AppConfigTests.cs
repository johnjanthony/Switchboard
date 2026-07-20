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
		Assert.Equal("http://localhost:9876/widget-status", cfg.ClaudeStatus.StatusUrl);
		Assert.Equal(5, cfg.ClaudeStatus.PollSeconds);
	}

	[Fact]
	public void ClaudeStatus_config_absent_block_loads_defaults()
	{
		var path = Path.Combine(Path.GetTempPath(), "wt-claudestatus-" + Guid.NewGuid().ToString("N") + ".json");
		try
		{
			File.WriteAllText(path, "{\"PollIntervalSeconds\":60}");
			var cfg = AppConfig.LoadFrom(path);
			Assert.Equal("http://localhost:9876/widget-status", cfg.ClaudeStatus.StatusUrl);
			Assert.Equal(5, cfg.ClaudeStatus.PollSeconds);
		}
		finally { if (File.Exists(path)) File.Delete(path); }
	}

	[Fact]
	public void Present_unreadable_config_is_degraded_and_not_overwritten()
	{
		var path = Path.Combine(Path.GetTempPath(), "wt-degraded-" + Guid.NewGuid().ToString("N") + ".json");
		File.WriteAllText(path, "{ this is not valid json ");
		try
		{
			var cfg = AppConfig.LoadFrom(path);
			Assert.True(cfg.LoadDegraded);
			cfg.WidgetX = 999;
			Assert.False(cfg.SaveTo(path));                                  // refuses to clobber
			Assert.Equal("{ this is not valid json ", File.ReadAllText(path)); // original preserved
		}
		finally { if (File.Exists(path)) File.Delete(path); }
	}

	[Fact]
	public void Absent_config_is_not_degraded_and_saves_atomically()
	{
		var path = Path.Combine(Path.GetTempPath(), "wt-absent-" + Guid.NewGuid().ToString("N") + ".json");
		try
		{
			var cfg = AppConfig.LoadFrom(path);
			Assert.False(cfg.LoadDegraded);
			Assert.True(cfg.SaveTo(path));
			Assert.True(File.Exists(path));
			Assert.False(File.Exists(path + ".tmp"));                        // temp cleaned up by the move
		}
		finally { foreach (var p in new[] { path, path + ".tmp" }) if (File.Exists(p)) File.Delete(p); }
	}
}
