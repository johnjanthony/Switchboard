using Switchboard.Watchtower.Core;
using Xunit;

public class AntigravityTranscriptParserTests
{
	static readonly DateTime Mtime = new(2026, 7, 23, 10, 0, 0, DateTimeKind.Utc);
	static readonly DateTime Now = new(2026, 7, 23, 10, 1, 0, DateTimeKind.Utc);

	[Fact]
	public void Parse_extracts_title_model_cwd_and_estimates_tokens()
	{
		var lines = new[]
		{
			"{\"step_index\":0,\"content\":\"<USER_REQUEST>\\nBuild new feature\\n</USER_REQUEST>\\n<USER_SETTINGS_CHANGE>The user changed setting `Model Selection` from None to Gemini 3.1 Pro (High).</USER_SETTINGS_CHANGE>\"}",
			"{\"step_index\":1,\"tool_calls\":[{\"name\":\"run_command\",\"args\":{\"Cwd\":\"c:\\\\Work\\\\Switchboard\"}}]}",
			"{\"step_index\":2,\"content\":\"<USER_SETTINGS_CHANGE>The user changed setting `Model Selection` from Gemini 3.1 Pro to Gemini 3.6 Flash (High).</USER_SETTINGS_CHANGE>\"}"
		};

		var model = AntigravityTranscriptParser.Parse(lines, "conv-1234", Mtime, Now, liveThresholdSeconds: 90);

		Assert.Equal("conv-1234", model.SessionId);
		Assert.Equal("Build new feature", model.Name);
		Assert.Equal("Gemini 3.6 Flash (High)", model.Model);
		Assert.Equal("Work/Switchboard", model.Label); // CwdLabeler.Label("c:\\Work\\Switchboard") -> "Work/Switchboard"
		Assert.Equal(1_000_000, model.WindowSize); // Gemini 3.6 Flash maps to 1M
		Assert.True(model.ContextTokens > 0);
		Assert.Equal(SessionStatus.Live, model.Status);
	}

	[Fact]
	public void ParseUserRequestTitle_extracts_and_truncates_first_line()
	{
		var content = "<USER_REQUEST>\nFix the bug in Switchboard\nSecond line\n</USER_REQUEST>";
		var title = AntigravityTranscriptParser.ParseUserRequestTitle(content);

		Assert.Equal("Fix the bug in Switchboard", title);
	}

	[Fact]
	public void ParseModelFromContent_extracts_model_name()
	{
		var content = "<USER_SETTINGS_CHANGE>The user changed setting `Model Selection` from None to Gemini 3.6 Flash (High). No need to comment.</USER_SETTINGS_CHANGE>";
		var model = AntigravityTranscriptParser.ParseModelFromContent(content);

		Assert.Equal("Gemini 3.6 Flash (High)", model);
	}
}
