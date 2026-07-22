using Switchboard.Watchtower.Core;
using Xunit;

public class WslSessionScannerTests
{
	static readonly DateTime Now = new(2026, 6, 12, 12, 0, 0, DateTimeKind.Utc);

	sealed class FakeLister(params string[] distros) : IDistroLister
	{
		public IReadOnlyList<string> RunningDistros() => distros;
	}

	[Fact]
	public void Parse_splits_strips_and_drops_blanks()
	{
		var raw = "Ubuntu-22.04\r\ndocker-desktop\r\n\r\n";
		Assert.Equal(new[] { "Ubuntu-22.04", "docker-desktop" }, WslDistroLister.Parse(raw));
	}

	[Fact]
	public void Skips_system_distros_and_globs_only_real_ones()
	{
		var lister = new FakeLister("Ubuntu-22.04", "docker-desktop", "docker-desktop-data");
		var globbed = new List<string>();

		IEnumerable<string> FakeGlob(string distro)
		{
			globbed.Add(distro);
			return distro == "Ubuntu-22.04" ? new[] { @"\\wsl.localhost\Ubuntu-22.04\home\janthony\.claude\projects\p\a.jsonl" } : Array.Empty<string>();
		}

		var found = WslSessionScanner.ActiveTranscripts(lister, Now, 5, FakeGlob,
			mtimeOf: _ => Now.AddSeconds(-20)).ToList();

		Assert.Equal(new[] { "Ubuntu-22.04" }, globbed);              // system distros skipped before globbing
		Assert.Single(found);
		Assert.Equal("Ubuntu-22.04", found[0].distro);
	}

	[Fact]
	public void Stale_wsl_transcripts_are_filtered()
	{
		var lister = new FakeLister("Ubuntu-22.04");
		IEnumerable<string> FakeGlob(string d) => new[] { @"\\wsl.localhost\Ubuntu-22.04\home\j\.claude\projects\p\a.jsonl" };
		var found = WslSessionScanner.ActiveTranscripts(lister, Now, 5, FakeGlob, mtimeOf: _ => Now.AddMinutes(-30)).ToList();
		Assert.Empty(found);
	}

	[Fact]
	public void MostRecentActivity_returns_newest_across_distros_skipping_system()
	{
		var lister = new FakeLister("Ubuntu-22.04", "docker-desktop");
		IEnumerable<string> FakeGlob(string d) => d == "Ubuntu-22.04" ? new[] { "a.jsonl", "b.jsonl" } : Array.Empty<string>();
		DateTime Mtime(string p) => p == "b.jsonl" ? Now.AddMinutes(-10) : Now.AddMinutes(-90);

		var t = WslSessionScanner.MostRecentActivityUtc(lister, FakeGlob, Mtime);
		Assert.Equal(Now.AddMinutes(-10), t);
	}

	[Fact]
	public void MostRecentActivity_null_when_nothing_globbed()
	{
		var lister = new FakeLister("Ubuntu-22.04");
		var t = WslSessionScanner.MostRecentActivityUtc(lister, _ => Array.Empty<string>(), _ => Now);
		Assert.Null(t);
	}

	[Fact]
	public void One_distros_glob_failure_does_not_abort_other_distros()
	{
		// A distro whose enumeration dies mid-glob contributes nothing; the scan continues.
		var lister = new FakeLister("bad-distro", "Ubuntu-22.04");
		IEnumerable<string> FakeGlob(string d)
		{
			if (d == "bad-distro") { yield return "half.jsonl"; throw new IOException("distro stopped"); }
			yield return @"\\wsl.localhost\Ubuntu-22.04\home\j\.claude\projects\p\a.jsonl";
		}

		var found = WslSessionScanner.ActiveTranscripts(lister, Now, 5, FakeGlob,
			mtimeOf: _ => Now.AddSeconds(-20)).ToList();

		Assert.Single(found);
		Assert.Equal("Ubuntu-22.04", found[0].distro);
	}

	[Fact]
	public void Retained_stem_bypasses_the_active_window()
	{
		var lister = new FakeLister("Ubuntu-22.04");
		IEnumerable<string> FakeGlob(string d) => new[] { @"\\wsl.localhost\Ubuntu-22.04\home\j\.claude\projects\p\needs-you-uuid.jsonl" };
		var retain = new HashSet<string> { "needs-you-uuid" };
		var found = WslSessionScanner.ActiveTranscripts(lister, Now, 5, FakeGlob, mtimeOf: _ => Now.AddMinutes(-30), retainIds: retain).ToList();
		Assert.Single(found);
		Assert.Equal("Ubuntu-22.04", found[0].distro);
	}
}
