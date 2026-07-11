using System;
using Switchboard.Watchtower.Core;
using Xunit;

public class TrayGaugeTests
{
	static SessionModel S(long tokens, long window, bool error = false) =>
		new("s", null, tokens, window, "m", SessionStatus.Live, DateTime.UtcNow, IsError: error);

	[Fact]
	public void Empty_list_is_zero_green_no_error()
	{
		var g = TrayGauge.From(Array.Empty<SessionModel>());
		Assert.Equal(0, g.Max);
		Assert.False(g.AnyError);
		Assert.Equal(Severity.Green, g.MaxSeverity);
	}

	[Fact]
	public void Picks_busiest_non_error_session()
	{
		var g = TrayGauge.From(new[] { S(40_000, 200_000), S(180_000, 200_000) });   // 20%, 90%
		Assert.Equal(0.9, g.Max, 3);
		Assert.Equal(Severity.Red, g.MaxSeverity);
		Assert.False(g.AnyError);
	}

	[Fact]
	public void Error_session_sets_AnyError_but_does_not_drive_max()
	{
		var g = TrayGauge.From(new[] { S(180_000, 200_000, error: true), S(40_000, 200_000) });
		Assert.True(g.AnyError);
		Assert.Equal(0.2, g.Max, 3);              // error session excluded from the max
		Assert.Equal(Severity.Green, g.MaxSeverity);
	}
}
