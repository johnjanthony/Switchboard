using Switchboard.Watchtower.Core;
using Xunit;

public class TranscriptTailTests
{
	static string TempFile(params string[] lines)
	{
		var path = Path.Combine(Path.GetTempPath(), "cctail-" + Guid.NewGuid().ToString("N") + ".jsonl");
		File.WriteAllText(path, string.Join('\n', lines) + "\n");
		return path;
	}

	[Fact]
	public void Returns_last_assistant_line_skipping_trailing_non_assistant_lines()
	{
		var asst = "{\"type\":\"assistant\",\"message\":{\"model\":\"m\",\"usage\":{\"input_tokens\":7}}}";
		var user = "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"x\"}}";
		var path = TempFile(user, asst, user);
		try
		{
			var line = TranscriptTail.LastAssistantLine(path);
			Assert.NotNull(line);
			Assert.Equal(7, TranscriptParser.ParseAssistantLine(line!)!.Usage.ContextTokens);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Returns_null_when_no_assistant_line_present()
	{
		var user = "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"x\"}}";
		var path = TempFile(user, user);
		try { Assert.Null(TranscriptTail.LastAssistantLine(path)); }
		finally { File.Delete(path); }
	}

	[Fact]
	public void Finds_assistant_line_pushed_beyond_the_small_tail_window()
	{
		var asst = "{\"type\":\"assistant\",\"message\":{\"model\":\"m\",\"usage\":{\"input_tokens\":42}}}";
		var giantUser = "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"" + new string('x', 200_000) + "\"}}";
		var path = TempFile(asst, giantUser);
		try
		{
			var line = TranscriptTail.LastAssistantLine(path);
			Assert.NotNull(line);
			Assert.Equal(42, TranscriptParser.ParseAssistantLine(line!)!.Usage.ContextTokens);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Skips_a_trailing_synthetic_line_to_the_last_real_turn()
	{
		var real = "{\"type\":\"assistant\",\"effort\":\"high\",\"message\":{\"model\":\"claude-opus-5-5\",\"usage\":{\"input_tokens\":900}}}";
		var synthetic = "{\"type\":\"assistant\",\"message\":{\"model\":\"<synthetic>\",\"usage\":{\"input_tokens\":0}}}";
		var path = TempFile(real, synthetic);
		try
		{
			var line = TranscriptTail.LastAssistantLine(path);
			Assert.NotNull(line);
			var turn = TranscriptParser.ParseAssistantLine(line!);
			Assert.NotNull(turn);
			Assert.Equal("claude-opus-5-5", turn!.Model);
			Assert.Equal("high", turn.Effort);
			Assert.Equal(900, turn.Usage.ContextTokens);
		}
		finally { File.Delete(path); }
	}
}
