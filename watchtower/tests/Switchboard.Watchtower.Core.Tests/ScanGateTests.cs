using System;
using Switchboard.Watchtower.Core;
using Xunit;

public class ScanGateTests
{
	static readonly DateTime T0 = new(2026, 7, 11, 12, 0, 0, DateTimeKind.Utc);
	static ScanGate Gate() => new(TimeSpan.FromMinutes(2));

	[Fact] public void Enter_succeeds_when_free() => Assert.True(Gate().TryEnter(T0));

	[Fact]
	public void Enter_blocked_while_in_flight()
	{
		var g = Gate();
		Assert.True(g.TryEnter(T0));
		Assert.False(g.TryEnter(T0.AddSeconds(30)));
	}

	[Fact]
	public void Exit_frees_immediately()
	{
		var g = Gate();
		g.TryEnter(T0);
		g.Exit();
		Assert.True(g.TryEnter(T0.AddSeconds(1)));
	}

	[Fact]
	public void Stale_entry_is_superseded_at_expiry()
	{
		var g = Gate();
		Assert.True(g.TryEnter(T0));
		Assert.True(g.TryEnter(T0.AddMinutes(2)));            // wedged scan superseded after 2 min
	}

	[Fact]
	public void Not_yet_stale_stays_blocked()
	{
		var g = Gate();
		Assert.True(g.TryEnter(T0));
		Assert.False(g.TryEnter(T0.AddMinutes(2).AddSeconds(-1)));
	}
}
