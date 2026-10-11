using Switchboard.Watchtower.Core;
using Xunit;

public class StatsSessionsTests
{
	static readonly DateTime Now = new(2026, 10, 10, 12, 0, 0, DateTimeKind.Utc);

	static StatsSession Item(string state = "active", string surface = "windows", string cwd = "C:/NoSuchRoot/alpha/beta",
		long? tokens = 412000, long? window = 1000000) =>
		new("s1", "Fix undo", cwd, surface, state, "Opus 5.5 high", "high", tokens, window);

	[Fact]
	public void A_live_session_maps_its_context_label_and_model()
	{
		var m = Assert.Single(StatsSessions.ToModels(new[] { Item() }, Now));
		Assert.Equal("alpha/beta", m.Label);
		Assert.Null(m.Distro);
		Assert.Equal(412000, m.ContextTokens);
		Assert.Equal(1000000, m.WindowSize);
		Assert.Equal(0.412, m.Pct, 3);
		Assert.Equal(SessionStatus.Live, m.Status);
		Assert.Equal(("s1", "Fix undo", "Opus 5.5 high", "high"), (m.SessionId, m.Name, m.Model, m.Effort));
		Assert.False(m.IsError);
	}

	[Theory]
	[InlineData("awaiting_human", SessionStatus.Live)]
	[InlineData("awaiting_agent", SessionStatus.Live)]
	[InlineData("idle", SessionStatus.Idle)]
	public void Waiting_states_are_live_and_idle_is_idle(string state, SessionStatus expected) =>
		Assert.Equal(expected, StatsSessions.ToModels(new[] { Item(state) }, Now).Single().Status);

	[Fact]
	public void A_wsl_session_carries_the_wsl_marker()
	{
		var m = StatsSessions.ToModels(new[] { Item(surface: "wsl", cwd: "/home/j/work/proj") }, Now).Single();
		Assert.Equal("WSL", m.Distro);
		Assert.Equal("work/proj", m.Label);
	}

	[Fact]
	public void A_session_that_has_not_reported_its_context_shows_empty()
	{
		var m = StatsSessions.ToModels(new[] { Item(tokens: null, window: null) }, Now).Single();
		Assert.Equal(0, m.Pct);
	}
}
