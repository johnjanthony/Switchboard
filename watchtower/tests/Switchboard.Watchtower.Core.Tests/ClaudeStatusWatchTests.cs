using Switchboard.Watchtower.Core;
using Xunit;

public class ClaudeStatusWatchTests
{
	static readonly DateTime T0 = new(2026, 6, 23, 12, 0, 0, DateTimeKind.Utc);

	static ClaudeStatus Status(ClaudeStatusLevel level, DateTime at, params string[] incidents)
		=> new(level, level.ToString(), incidents, at);

	[Fact]
	public void Idle_operational_check_stays_idle()
	{
		var w = new ClaudeStatusWatch();
		var action = w.ApplyFetch(Status(ClaudeStatusLevel.Operational, T0), T0);
		Assert.Equal(WatchAction.None, action);
		Assert.Equal(ClaudeWatchState.Idle, w.State);
		Assert.False(w.Snapshot().DotVisible);
		Assert.True(w.Snapshot().HasData);
	}

	[Fact]
	public void Idle_degraded_check_starts_watching()
	{
		var w = new ClaudeStatusWatch();
		var action = w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		Assert.Equal(WatchAction.StartPolling, action);
		Assert.Equal(ClaudeWatchState.Watching, w.State);
		var v = w.Snapshot();
		Assert.True(v.DotVisible);
		Assert.Equal(ClaudeStatusLevel.Major, v.DotLevel);
		Assert.Equal(ClaudeStatusButton.StopWatching, v.Button);
	}

	[Fact]
	public void Idle_unknown_check_stays_idle()
	{
		var w = new ClaudeStatusWatch();
		var action = w.ApplyFetch(ClaudeStatus.Unknown(T0), T0);
		Assert.Equal(WatchAction.None, action);
		Assert.Equal(ClaudeWatchState.Idle, w.State);
	}

	[Fact]
	public void Watching_operational_poll_resolves_and_stops()
	{
		var w = new ClaudeStatusWatch();
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		var action = w.ApplyFetch(Status(ClaudeStatusLevel.Operational, T0.AddMinutes(2)), T0.AddMinutes(2));
		Assert.Equal(WatchAction.StopPolling, action);
		Assert.Equal(ClaudeWatchState.ResolvedUnacked, w.State);
		var v = w.Snapshot();
		Assert.True(v.DotVisible);
		Assert.Equal(ClaudeStatusLevel.Operational, v.DotLevel);
		Assert.Equal(ClaudeStatusButton.Clear, v.Button);
	}

	[Fact]
	public void Watching_degraded_poll_keeps_watching()
	{
		var w = new ClaudeStatusWatch();
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		var action = w.ApplyFetch(Status(ClaudeStatusLevel.Minor, T0.AddMinutes(2)), T0.AddMinutes(2));
		Assert.Equal(WatchAction.None, action);
		Assert.Equal(ClaudeWatchState.Watching, w.State);
		Assert.Equal(ClaudeStatusLevel.Minor, w.Snapshot().DotLevel);
	}

	[Fact]
	public void Watching_unknown_poll_keeps_watching()
	{
		var w = new ClaudeStatusWatch();
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		var action = w.ApplyFetch(ClaudeStatus.Unknown(T0.AddMinutes(2)), T0.AddMinutes(2));
		Assert.Equal(WatchAction.None, action);
		Assert.Equal(ClaudeWatchState.Watching, w.State);
	}

	[Fact]
	public void Watching_past_cap_while_degraded_caps_and_stops()
	{
		var w = new ClaudeStatusWatch(maxWatchMinutes: 180);
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		var action = w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0.AddMinutes(181)), T0.AddMinutes(181));
		Assert.Equal(WatchAction.StopPolling, action);
		Assert.Equal(ClaudeWatchState.CappedUnacked, w.State);
		var v = w.Snapshot();
		Assert.True(v.DotVisible);
		Assert.Equal(ClaudeStatusLevel.Major, v.DotLevel);
		Assert.Equal(ClaudeStatusButton.Clear, v.Button);
	}

	[Fact]
	public void Acknowledge_from_resolved_returns_to_idle()
	{
		var w = new ClaudeStatusWatch();
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		w.ApplyFetch(Status(ClaudeStatusLevel.Operational, T0.AddMinutes(2)), T0.AddMinutes(2));
		var action = w.Acknowledge();
		Assert.Equal(WatchAction.StopPolling, action);
		Assert.Equal(ClaudeWatchState.Idle, w.State);
		Assert.False(w.Snapshot().DotVisible);
		Assert.Equal(ClaudeStatusButton.CheckNow, w.Snapshot().Button);
	}

	[Fact]
	public void Acknowledge_from_watching_dismisses_to_idle()
	{
		var w = new ClaudeStatusWatch();
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0), T0);
		var action = w.Acknowledge();
		Assert.Equal(WatchAction.StopPolling, action);
		Assert.Equal(ClaudeWatchState.Idle, w.State);
	}

	[Fact]
	public void Snapshot_before_any_fetch_has_no_data()
	{
		var v = new ClaudeStatusWatch().Snapshot();
		Assert.False(v.DotVisible);
		Assert.False(v.HasData);
		Assert.Equal(ClaudeStatusButton.CheckNow, v.Button);
		Assert.Null(v.FetchedAtUtc);
	}

	[Fact]
	public void Snapshot_carries_incident_names()
	{
		var w = new ClaudeStatusWatch();
		w.ApplyFetch(Status(ClaudeStatusLevel.Major, T0, "Elevated errors"), T0);
		Assert.Equal(new[] { "Elevated errors" }, w.Snapshot().IncidentNames);
	}
}
