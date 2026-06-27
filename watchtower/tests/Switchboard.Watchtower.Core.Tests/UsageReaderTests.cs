using Switchboard.Watchtower.Core;
using Xunit;

public class UsageReaderTests
{
	static readonly DateTime Now = new(2026, 6, 12, 12, 0, 0, DateTimeKind.Utc);

	static string TempFile(string content)
	{
		var path = Path.Combine(Path.GetTempPath(), "ccread-" + Guid.NewGuid().ToString("N") + ".jsonl");
		File.WriteAllText(path, content + "\n");
		File.SetLastWriteTimeUtc(path, Now.AddSeconds(-30));
		return path;
	}

	[Fact]
	public void Read_builds_session_model_from_last_assistant_turn()
	{
		var line = "{\"type\":\"assistant\",\"cwd\":\"/home/janthony/work/rpdm\",\"message\":{\"model\":\"claude-opus-4-8[1m]\",\"usage\":{\"input_tokens\":10,\"cache_creation_input_tokens\":500000,\"cache_read_input_tokens\":300000,\"output_tokens\":5}}}";
		var path = TempFile(line);
		try
		{
			var m = UsageReader.Read(path, distro: "Ubuntu-22.04", nowUtc: Now, liveThresholdSeconds: 90);
			Assert.Equal("work/rpdm", m.Label);
			Assert.Equal("Ubuntu-22.04", m.Distro);
			Assert.Equal(800_010, m.ContextTokens);
			Assert.Equal(1_000_000, m.WindowSize);
			Assert.Equal(SessionStatus.Live, m.Status);
			Assert.False(m.IsError);
			Assert.InRange(m.Pct, 0.80, 0.81);
			Assert.Equal(Severity.Red, m.Severity);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Read_throws_when_no_assistant_turn_found()
	{
		var path = TempFile("{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"x\"}}");
		try { Assert.Throws<InvalidDataException>(() => UsageReader.Read(path, null, Now, 90)); }
		finally { File.Delete(path); }
	}

	[Fact]
	public void Read_sets_session_id_from_transcript_filename()
	{
		var line = "{\"type\":\"assistant\",\"cwd\":\"/home/janthony/work/rpdm\",\"message\":{\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":10,\"output_tokens\":5}}}";
		var path = TempFile(line);
		try
		{
			var m = UsageReader.Read(path, distro: null, nowUtc: Now, liveThresholdSeconds: 90);
			Assert.Equal(Path.GetFileNameWithoutExtension(path), m.SessionId);
		}
		finally { File.Delete(path); }
	}
}
