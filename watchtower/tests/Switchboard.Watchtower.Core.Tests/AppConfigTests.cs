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
}
