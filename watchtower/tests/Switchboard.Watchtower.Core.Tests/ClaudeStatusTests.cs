using Switchboard.Watchtower.Core;
using Xunit;

public class ClaudeStatusTests
{
	static readonly DateTime At = new(2026, 6, 23, 12, 0, 0, DateTimeKind.Utc);

	[Theory]
	[InlineData("none", ClaudeStatusLevel.Operational)]
	[InlineData("minor", ClaudeStatusLevel.Minor)]
	[InlineData("major", ClaudeStatusLevel.Major)]
	[InlineData("critical", ClaudeStatusLevel.Critical)]
	public void Parse_maps_indicator_to_level(string indicator, ClaudeStatusLevel expected)
	{
		var json = $"{{\"status\":{{\"indicator\":\"{indicator}\",\"description\":\"X\"}},\"incidents\":[]}}";
		var s = ClaudeStatus.Parse(json, At);
		Assert.NotNull(s);
		Assert.Equal(expected, s!.Level);
	}

	[Fact]
	public void Parse_reads_description_and_fetched_at()
	{
		var json = "{\"status\":{\"indicator\":\"none\",\"description\":\"All Systems Operational\"},\"incidents\":[]}";
		var s = ClaudeStatus.Parse(json, At);
		Assert.NotNull(s);
		Assert.Equal("All Systems Operational", s!.Description);
		Assert.Equal(At, s.FetchedAtUtc);
	}

	[Fact]
	public void Parse_collects_only_unresolved_incident_names()
	{
		var json = "{\"status\":{\"indicator\":\"minor\",\"description\":\"Degraded\"},\"incidents\":["
			+ "{\"name\":\"Elevated errors\",\"status\":\"investigating\"},"
			+ "{\"name\":\"Old thing\",\"status\":\"resolved\"},"
			+ "{\"name\":\"Wrapped up\",\"status\":\"postmortem\"}]}";
		var s = ClaudeStatus.Parse(json, At);
		Assert.NotNull(s);
		Assert.Equal(new[] { "Elevated errors" }, s!.IncidentNames);
	}

	[Fact]
	public void Parse_handles_empty_incidents()
	{
		var json = "{\"status\":{\"indicator\":\"none\",\"description\":\"All Systems Operational\"},\"incidents\":[]}";
		var s = ClaudeStatus.Parse(json, At);
		Assert.NotNull(s);
		Assert.Empty(s!.IncidentNames);
	}

	[Fact]
	public void Parse_maps_unrecognized_indicator_to_unknown_level()
	{
		var json = "{\"status\":{\"indicator\":\"weird\",\"description\":\"?\"},\"incidents\":[]}";
		var s = ClaudeStatus.Parse(json, At);
		Assert.NotNull(s);
		Assert.Equal(ClaudeStatusLevel.Unknown, s!.Level);
	}

	[Fact]
	public void Parse_returns_null_when_status_object_missing()
	{
		Assert.Null(ClaudeStatus.Parse("{\"incidents\":[]}", At));
	}

	[Fact]
	public void Parse_returns_null_when_indicator_missing()
	{
		Assert.Null(ClaudeStatus.Parse("{\"status\":{\"description\":\"X\"}}", At));
	}

	[Fact]
	public void Parse_returns_null_on_malformed_json()
	{
		Assert.Null(ClaudeStatus.Parse("not json", At));
	}

	[Fact]
	public void Unknown_factory_has_unknown_level_and_empty_incidents()
	{
		var s = ClaudeStatus.Unknown(At);
		Assert.Equal(ClaudeStatusLevel.Unknown, s.Level);
		Assert.Empty(s.IncidentNames);
		Assert.Equal(At, s.FetchedAtUtc);
	}
}
