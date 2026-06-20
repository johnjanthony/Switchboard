using Switchboard.Watchtower.Core;
using Xunit;

public class WindowsSessionScannerTests
{
	static readonly DateTime Now = new(2026, 6, 12, 12, 0, 0, DateTimeKind.Utc);

	[Fact]
	public void Lists_recent_top_level_transcripts_excludes_subagents_and_stale()
	{
		var root = Path.Combine(Path.GetTempPath(), "ccproj-" + Guid.NewGuid().ToString("N"));
		var projA = Path.Combine(root, "C--Work");
		var subagents = Path.Combine(projA, "session-uuid", "subagents");
		Directory.CreateDirectory(subagents);

		var recent = Path.Combine(projA, "aaaa.jsonl");
		File.WriteAllText(recent, "{}\n");
		File.SetLastWriteTimeUtc(recent, Now.AddSeconds(-30));

		var stale = Path.Combine(projA, "bbbb.jsonl");
		File.WriteAllText(stale, "{}\n");
		File.SetLastWriteTimeUtc(stale, Now.AddMinutes(-30));

		var sub = Path.Combine(subagents, "agent-x.jsonl");
		File.WriteAllText(sub, "{}\n");
		File.SetLastWriteTimeUtc(sub, Now.AddSeconds(-10));

		try
		{
			var found = WindowsSessionScanner.ActiveTranscripts(root, Now, activeWindowMinutes: 5).ToList();
			Assert.Single(found);
			Assert.Equal(recent, found[0]);
		}
		finally { Directory.Delete(root, recursive: true); }
	}

	[Fact]
	public void Missing_root_yields_nothing()
	{
		var missing = Path.Combine(Path.GetTempPath(), "no-such-" + Guid.NewGuid().ToString("N"));
		Assert.Empty(WindowsSessionScanner.ActiveTranscripts(missing, Now, 5));
	}

	[Fact]
	public void MostRecentActivity_returns_newest_mtime_ignoring_window()
	{
		var root = Path.Combine(Path.GetTempPath(), "ccproj-" + Guid.NewGuid().ToString("N"));
		var projA = Path.Combine(root, "C--Work");
		Directory.CreateDirectory(projA);

		var older = Path.Combine(projA, "aaaa.jsonl");
		File.WriteAllText(older, "{}\n");
		File.SetLastWriteTimeUtc(older, Now.AddMinutes(-90));   // well past any active window

		var newer = Path.Combine(projA, "bbbb.jsonl");
		File.WriteAllText(newer, "{}\n");
		File.SetLastWriteTimeUtc(newer, Now.AddMinutes(-25));

		try
		{
			var t = WindowsSessionScanner.MostRecentActivityUtc(root);
			Assert.NotNull(t);
			Assert.Equal(Now.AddMinutes(-25), t!.Value, TimeSpan.FromSeconds(2));
		}
		finally { Directory.Delete(root, recursive: true); }
	}

	[Fact]
	public void MostRecentActivity_null_when_no_transcripts()
	{
		var missing = Path.Combine(Path.GetTempPath(), "no-such-" + Guid.NewGuid().ToString("N"));
		Assert.Null(WindowsSessionScanner.MostRecentActivityUtc(missing));
	}
}
