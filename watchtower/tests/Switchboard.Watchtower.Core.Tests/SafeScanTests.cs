using Switchboard.Watchtower.Core;
using Xunit;

public class SafeScanTests
{
	static IEnumerable<string> ThrowsMidEnumeration()
	{
		yield return "first.jsonl";
		throw new IOException("directory handle died mid-enumeration");
	}

	[Fact]
	public void Materialize_returns_files_from_healthy_enumeration()
	{
		var files = SafeScan.Materialize(() => new[] { "a.jsonl", "b.jsonl" });
		Assert.Equal(new[] { "a.jsonl", "b.jsonl" }, files);
	}

	[Fact]
	public void Materialize_swallows_midenumeration_failure_yielding_nothing()
	{
		// The try must cover MoveNext, not just the enumerable's construction: a lazy
		// Directory.EnumerateFiles opens its directory handle at the first MoveNext.
		var files = SafeScan.Materialize(ThrowsMidEnumeration);
		Assert.Empty(files);
	}

	[Fact]
	public void WithMtimes_skips_files_whose_mtime_read_fails()
	{
		var paths = new[] { "ok1.jsonl", "gone.jsonl", "ok2.jsonl" };
		DateTime Mtime(string p) => p == "gone.jsonl"
			? throw new IOException("file vanished")
			: new DateTime(2026, 7, 15, 12, 0, 0, DateTimeKind.Utc);

		var pairs = SafeScan.WithMtimes(paths, Mtime).ToList();
		Assert.Equal(new[] { "ok1.jsonl", "ok2.jsonl" }, pairs.Select(p => p.Path));
	}
}
