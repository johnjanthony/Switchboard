using Switchboard.Watchtower.Core;
using Xunit;

public class SessionAggregatorTests
{
	static readonly DateTime Now = new(2026, 6, 12, 12, 0, 0, DateTimeKind.Utc);

	static string TempTranscript(long input, string model, int secondsAgo)
	{
		var line = $"{{\"type\":\"assistant\",\"cwd\":\"C:\\\\Work\\\\p{input}\",\"message\":{{\"model\":\"{model}\",\"usage\":{{\"input_tokens\":{input},\"cache_creation_input_tokens\":0,\"cache_read_input_tokens\":0,\"output_tokens\":0}}}}}}";
		var path = Path.Combine(Path.GetTempPath(), "ccagg-" + Guid.NewGuid().ToString("N") + ".jsonl");
		File.WriteAllText(path, line + "\n");
		File.SetLastWriteTimeUtc(path, Now.AddSeconds(-secondsAgo));
		return path;
	}

	[Fact]
	public void Collect_merges_sources_and_sorts_busiest_first()
	{
		var low = TempTranscript(40_000, "claude-sonnet-4-6", 20);   // 20% of 200K
		var high = TempTranscript(180_000, "claude-sonnet-4-6", 20); // 90% of 200K
		var errors = new List<string>();
		try
		{
			var result = SessionAggregator.Collect(
				new[] { low },
				new[] { ("Ubuntu-22.04", high) },
				Now, liveThresholdSeconds: 90,
				onError: (p, e) => errors.Add(p));

			Assert.Equal(2, result.Count);
			Assert.True(result[0].Pct >= result[1].Pct);   // busiest first
			Assert.Equal("Ubuntu-22.04", result[0].Distro);
			Assert.Empty(errors);
		}
		finally { File.Delete(low); File.Delete(high); }
	}

	[Fact]
	public void Collect_turns_unreadable_files_into_error_models_and_reports()
	{
		var bad = Path.Combine(Path.GetTempPath(), "ccagg-bad-" + Guid.NewGuid().ToString("N") + ".jsonl");
		File.WriteAllText(bad, "{\"type\":\"user\",\"message\":{\"role\":\"user\"}}\n"); // no assistant turn
		File.SetLastWriteTimeUtc(bad, Now.AddSeconds(-10));
		var errors = new List<string>();
		try
		{
			var result = SessionAggregator.Collect(new[] { bad }, Array.Empty<(string, string)>(), Now, 90, (p, e) => errors.Add(p));
			Assert.Single(result);
			Assert.True(result[0].IsError);
			Assert.Single(errors);
		}
		finally { File.Delete(bad); }
	}

	[Fact]
	public void Collect_handles_antigravity_transcripts_and_error_derived_session_id()
	{
		var dir = Path.Combine(Path.GetTempPath(), "agagg-" + Guid.NewGuid().ToString("N"), "uuid-7777", ".system_generated", "logs");
		Directory.CreateDirectory(dir);
		var badAg = Path.Combine(dir, "transcript_full.jsonl");
		File.WriteAllText(badAg, ""); // empty file -> throws InvalidDataException
		File.SetLastWriteTimeUtc(badAg, Now.AddSeconds(-10));

		var errors = new List<string>();
		try
		{
			var result = SessionAggregator.Collect(
				Array.Empty<string>(),
				Array.Empty<(string, string)>(),
				new[] { badAg },
				Now, 90, (p, e) => errors.Add(p));

			Assert.Single(result);
			Assert.True(result[0].IsError);
			Assert.Equal("uuid-7777", result[0].SessionId); // directory-derived SessionId, not "transcript_full"
			Assert.Single(errors);
		}
		finally
		{
			try { Directory.Delete(Path.GetDirectoryName(Path.GetDirectoryName(dir)!), recursive: true); } catch { }
		}
	}
}
