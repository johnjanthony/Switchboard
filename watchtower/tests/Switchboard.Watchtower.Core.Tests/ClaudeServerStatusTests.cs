using Switchboard.Watchtower.Core;
using Xunit;

public class ClaudeServerStatusTests
{
	[Fact]
	public void ParseView_maps_watching_major()
	{
		var json = "{\"watch_state\":\"watching\",\"dot_visible\":true,\"level\":\"major\",\"has_data\":true,\"description\":\"Partial outage\",\"incidents\":[\"X\"],\"fetched_at\":\"2026-06-25T12:00:00+00:00\",\"button\":\"stop\"}";
		var v = ClaudeServerStatus.ParseView(json);
		Assert.True(v.DotVisible);
		Assert.Equal(ClaudeStatusLevel.Major, v.DotLevel);
		Assert.True(v.HasData);
		Assert.Equal("Partial outage", v.Description);
		Assert.Equal(new[] { "X" }, v.IncidentNames);
		Assert.Equal(ClaudeStatusButton.StopWatching, v.Button);
		Assert.NotNull(v.FetchedAtUtc);
	}

	[Fact]
	public void ParseView_maps_idle_operational_check()
	{
		var json = "{\"watch_state\":\"idle\",\"dot_visible\":false,\"level\":\"operational\",\"has_data\":false,\"description\":\"\",\"incidents\":[],\"fetched_at\":null,\"button\":\"check\"}";
		var v = ClaudeServerStatus.ParseView(json);
		Assert.False(v.DotVisible);
		Assert.Equal(ClaudeStatusLevel.Operational, v.DotLevel);
		Assert.Equal(ClaudeStatusButton.CheckNow, v.Button);
		Assert.Null(v.FetchedAtUtc);
		Assert.Empty(v.IncidentNames);
	}

	[Fact]
	public void ParseView_maps_clear_button_and_resolved_level()
	{
		var json = "{\"watch_state\":\"resolved_unacked\",\"dot_visible\":true,\"level\":\"operational\",\"has_data\":true,\"description\":\"Recovered\",\"incidents\":[],\"fetched_at\":\"2026-06-25T12:00:00+00:00\",\"button\":\"clear\"}";
		var v = ClaudeServerStatus.ParseView(json);
		Assert.Equal(ClaudeStatusButton.Clear, v.Button);
		Assert.Equal(ClaudeStatusLevel.Operational, v.DotLevel);
	}

	[Fact]
	public void ParseView_malformed_is_hidden_idle()
	{
		var v = ClaudeServerStatus.ParseView("not json");
		Assert.False(v.DotVisible);
		Assert.Equal(ClaudeStatusButton.CheckNow, v.Button);
		Assert.Equal(ClaudeStatusLevel.Unknown, v.DotLevel);
	}

	[Fact]
	public void ParseView_unrecognized_level_is_unknown()
	{
		var json = "{\"watch_state\":\"watching\",\"dot_visible\":true,\"level\":\"weird\",\"has_data\":true,\"description\":\"\",\"incidents\":[],\"fetched_at\":null,\"button\":\"stop\"}";
		var v = ClaudeServerStatus.ParseView(json);
		Assert.Equal(ClaudeStatusLevel.Unknown, v.DotLevel);
	}
}
