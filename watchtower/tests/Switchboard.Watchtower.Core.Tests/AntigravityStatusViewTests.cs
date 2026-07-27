using System;
using Switchboard.Watchtower.Core;
using Xunit;

namespace Switchboard.Watchtower.Core.Tests;

public class AntigravityStatusViewTests
{
	[Fact]
	public void ParseView_EmptyJson_ReturnsHidden()
	{
		var view = AntigravityServerStatus.ParseView("");
		Assert.False(view.DotVisible);
		Assert.Equal(AntigravityStatusLevel.Unknown, view.DotLevel);
		Assert.False(view.HasData);
		Assert.Equal(AntigravityStatusButton.CheckNow, view.Button);
	}

	[Fact]
	public void ParseView_ValidJson_ParsesCorrectly()
	{
		string json = """
		{
			"watch_state": "watching",
			"dot_visible": true,
			"level": "minor",
			"has_data": true,
			"description": "Google Cloud: 1 active incident(s)",
			"incidents": ["Vertex AI Degraded"],
			"fetched_at": "2026-07-27T17:00:00Z",
			"button": "stop",
			"local_lsp_healthy": false
		}
		""";

		var view = AntigravityServerStatus.ParseView(json);
		Assert.True(view.DotVisible);
		Assert.Equal(AntigravityStatusLevel.Minor, view.DotLevel);
		Assert.True(view.HasData);
		Assert.Equal("Google Cloud: 1 active incident(s)", view.Description);
		Assert.Single(view.IncidentNames);
		Assert.Equal("Vertex AI Degraded", view.IncidentNames[0]);
		Assert.Equal(AntigravityStatusButton.StopWatching, view.Button);
		Assert.False(view.LocalLspHealthy);
	}
}
