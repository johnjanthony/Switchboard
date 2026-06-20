using Switchboard.Watchtower.Core;
using Xunit;

public class TranscriptParserTests
{
	const string AssistantLine =
		"{\"type\":\"assistant\",\"cwd\":\"C:\\\\Work\\\\rpdm\",\"message\":{\"role\":\"assistant\",\"model\":\"claude-opus-4-8[1m]\",\"usage\":{\"input_tokens\":10,\"cache_creation_input_tokens\":500000,\"cache_read_input_tokens\":300000,\"output_tokens\":200}}}";

	[Fact]
	public void Parses_model_usage_and_cwd_from_assistant_line()
	{
		var turn = TranscriptParser.ParseAssistantLine(AssistantLine);
		Assert.NotNull(turn);
		Assert.Equal("claude-opus-4-8[1m]", turn!.Model);
		Assert.Equal("C:\\Work\\rpdm", turn.Cwd);
		Assert.Equal(800_010, turn.Usage.ContextTokens);
	}

	[Fact]
	public void Returns_null_for_user_line_without_usage()
	{
		var line = "{\"type\":\"user\",\"cwd\":\"C:\\\\Work\",\"message\":{\"role\":\"user\",\"content\":\"hi\"}}";
		Assert.Null(TranscriptParser.ParseAssistantLine(line));
	}

	[Fact]
	public void Returns_null_for_malformed_or_empty_line()
	{
		Assert.Null(TranscriptParser.ParseAssistantLine("{ this is not json"));
		Assert.Null(TranscriptParser.ParseAssistantLine(""));
	}

	[Fact]
	public void Missing_usage_fields_default_to_zero()
	{
		var line = "{\"type\":\"assistant\",\"message\":{\"model\":\"m\",\"usage\":{\"input_tokens\":5}}}";
		var turn = TranscriptParser.ParseAssistantLine(line);
		Assert.NotNull(turn);
		Assert.Equal(5, turn!.Usage.ContextTokens);
	}
}
