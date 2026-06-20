using Switchboard.Watchtower.Core;
using Xunit;

namespace Switchboard.Watchtower.Core.Tests;

public class LegacyConfigMigrationTests
{
	static string NewTempDir()
	{
		var dir = Path.Combine(Path.GetTempPath(), "sbwt-test-" + Guid.NewGuid().ToString("N"));
		Directory.CreateDirectory(dir);
		return dir;
	}

	[Fact]
	public void Migrates_legacy_config_when_new_is_absent()
	{
		var root = NewTempDir();
		var legacy = Path.Combine(root, "ClaudeContextWidget", "config.json");
		var current = Path.Combine(root, "Switchboard", "Watchtower", "config.json");
		Directory.CreateDirectory(Path.GetDirectoryName(legacy)!);
		File.WriteAllText(legacy, "{\"PollIntervalSeconds\":42}");

		var migrated = AppConfig.MigrateLegacyConfig(legacy, current);

		Assert.True(migrated);
		Assert.True(File.Exists(current));
		Assert.Equal(42, AppConfig.LoadFrom(current).PollIntervalSeconds);
	}

	[Fact]
	public void Does_not_overwrite_existing_new_config()
	{
		var root = NewTempDir();
		var legacy = Path.Combine(root, "ClaudeContextWidget", "config.json");
		var current = Path.Combine(root, "Switchboard", "Watchtower", "config.json");
		Directory.CreateDirectory(Path.GetDirectoryName(legacy)!);
		Directory.CreateDirectory(Path.GetDirectoryName(current)!);
		File.WriteAllText(legacy, "{\"PollIntervalSeconds\":42}");
		File.WriteAllText(current, "{\"PollIntervalSeconds\":7}");

		var migrated = AppConfig.MigrateLegacyConfig(legacy, current);

		Assert.False(migrated);
		Assert.Equal(7, AppConfig.LoadFrom(current).PollIntervalSeconds);
	}

	[Fact]
	public void No_op_when_no_legacy_config()
	{
		var root = NewTempDir();
		var legacy = Path.Combine(root, "ClaudeContextWidget", "config.json");
		var current = Path.Combine(root, "Switchboard", "Watchtower", "config.json");

		var migrated = AppConfig.MigrateLegacyConfig(legacy, current);

		Assert.False(migrated);
		Assert.False(File.Exists(current));
	}
}
