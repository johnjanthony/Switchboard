using Switchboard.Watchtower.Core;
using Xunit;

public class TranscriptTitleTests
{
	const string Assistant =
		"{\"type\":\"assistant\",\"message\":{\"role\":\"assistant\",\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"cache_creation_input_tokens\":0,\"cache_read_input_tokens\":0,\"output_tokens\":1}}}";

	static string TempFile(params string[] lines)
	{
		var path = Path.Combine(Path.GetTempPath(), "cctitle-" + Guid.NewGuid().ToString("N") + ".jsonl");
		File.WriteAllText(path, string.Join('\n', lines) + "\n");
		return path;
	}

	[Fact]
	public void Last_ai_title_wins()
	{
		var path = TempFile(
			"{\"type\":\"ai-title\",\"sessionId\":\"s1\",\"aiTitle\":\"First\"}",
			Assistant,
			"{\"type\":\"ai-title\",\"sessionId\":\"s1\",\"aiTitle\":\"Second\"}");
		try
		{
			var (name, source) = TranscriptTitles.Read(path, "s1");
			Assert.Equal("Second", name);
			Assert.Equal("ai-title", source);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Custom_title_outranks_later_ai_title()
	{
		var path = TempFile(
			"{\"type\":\"custom-title\",\"sessionId\":\"s2\",\"customTitle\":\"Named\"}",
			"{\"type\":\"ai-title\",\"sessionId\":\"s2\",\"aiTitle\":\"Auto\"}");
		try
		{
			var (name, source) = TranscriptTitles.Read(path, "s2");
			Assert.Equal("Named", name);
			Assert.Equal("custom-title", source);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void No_title_returns_nulls()
	{
		var path = TempFile(Assistant);
		try
		{
			var (name, source) = TranscriptTitles.Read(path, "s3");
			Assert.Null(name);
			Assert.Null(source);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Appended_title_is_picked_up_incrementally()
	{
		var path = TempFile("{\"type\":\"ai-title\",\"sessionId\":\"s4\",\"aiTitle\":\"Old\"}");
		try
		{
			var first = TranscriptTitles.Read(path, "s4");
			Assert.Equal("Old", first.Name);
			File.AppendAllText(path, "{\"type\":\"ai-title\",\"sessionId\":\"s4\",\"aiTitle\":\"New\"}\n");
			var second = TranscriptTitles.Read(path, "s4");
			Assert.Equal("New", second.Name);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Partial_trailing_line_is_not_consumed_until_its_newline_arrives()
	{
		var path = Path.Combine(Path.GetTempPath(), "cctitle-partial-" + Guid.NewGuid().ToString("N") + ".jsonl");
		File.WriteAllText(path,
			"{\"type\":\"ai-title\",\"sessionId\":\"p1\",\"aiTitle\":\"First\"}\n" +
			"{\"type\":\"ai-title\",\"sessionId\":\"p1\",\"aiTitle\":\"Second\"}");   // no trailing newline
		try
		{
			Assert.Equal("First", TranscriptTitles.Read(path, "p1").Name);           // partial line skipped
			File.AppendAllText(path, "\n");                                          // complete the line
			Assert.Equal("Second", TranscriptTitles.Read(path, "p1").Name);          // now consumed
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Cache_is_bounded()
	{
		for (int i = 0; i < TranscriptTitles.MaxCacheEntries + 50; i++)
		{
			var path = Path.Combine(Path.GetTempPath(), $"cctitle-bound-{i}-" + Guid.NewGuid().ToString("N") + ".jsonl");
			File.WriteAllText(path, $"{{\"type\":\"ai-title\",\"sessionId\":\"b{i}\",\"aiTitle\":\"T{i}\"}}\n");
			try { TranscriptTitles.Read(path, $"b{i}"); } finally { File.Delete(path); }
		}
		Assert.True(TranscriptTitles.CacheEntryCount <= TranscriptTitles.MaxCacheEntries);
	}
}
