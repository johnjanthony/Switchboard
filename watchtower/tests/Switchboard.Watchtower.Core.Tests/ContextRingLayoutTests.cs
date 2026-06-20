using System;
using System.Linq;
using Switchboard.Watchtower.Core;
using Xunit;

public class ContextRingLayoutTests
{
	// Pct = ContextTokens / WindowSize, so pct*1000 over 1000 reproduces pct exactly.
	static SessionModel Sess(double pct, bool error = false) =>
		new("s", null, (long)(pct * 1000), 1000, "claude", SessionStatus.Live, DateTime.UnixEpoch, error);

	[Fact]
	public void Empty_session_list_yields_no_rings()
	{
		var r = ContextRingLayout.Build(Array.Empty<SessionModel>(), 0f, 34f);
		Assert.Empty(r.Rings);
		Assert.Equal(0, r.Overflow);
	}

	[Fact]
	public void Single_session_centered_ring_with_proportional_sweep()
	{
		// height 34 -> dMax 26, penInset 2.25, od 21.5, clusterTop 4 -> ring at (2.25, 6.25, 21.5, 21.5).
		var r = ContextRingLayout.Build(new[] { Sess(0.5) }, 0f, 34f);
		var ring = Assert.Single(r.Rings);
		Assert.Equal(180f, ring.SweepDegrees, 3);
		Assert.Equal(2.25f, ring.Bounds.X, 3);
		Assert.Equal(6.25f, ring.Bounds.Y, 3);
		Assert.Equal(21.5f, ring.Bounds.Width, 3);
		Assert.Equal(21.5f, ring.Bounds.Height, 3);
		Assert.Equal(0, r.Overflow);
	}

	[Fact]
	public void Rings_sorted_fullest_outermost()
	{
		var r = ContextRingLayout.Build(new[] { Sess(0.2), Sess(0.8), Sess(0.5) }, 0f, 34f);
		Assert.Equal(3, r.Rings.Count);
		Assert.Equal(0.8, r.Rings[0].Pct, 3);
		Assert.Equal(0.5, r.Rings[1].Pct, 3);
		Assert.Equal(0.2, r.Rings[2].Pct, 3);
		Assert.True(r.Rings[0].Bounds.Width > r.Rings[1].Bounds.Width);   // outer ring is largest
	}

	[Fact]
	public void Error_session_takes_outer_ring_and_full_sweep()
	{
		var r = ContextRingLayout.Build(new[] { Sess(0.9), Sess(0.1, error: true) }, 0f, 34f);
		Assert.True(r.Rings[0].IsError);
		Assert.Equal(360f, r.Rings[0].SweepDegrees, 3);
	}

	[Fact]
	public void Excess_sessions_capped_with_overflow_count()
	{
		// height 34 fits 3 rings; 5 sessions -> 3 rings, overflow 2.
		var sessions = new[] { Sess(0.9), Sess(0.8), Sess(0.7), Sess(0.6), Sess(0.5) };
		var r = ContextRingLayout.Build(sessions, 0f, 34f);
		Assert.Equal(3, r.Rings.Count);
		Assert.Equal(2, r.Overflow);
	}

	[Fact]
	public void Visible_rings_never_exceed_maxRings()
	{
		// Thin rings make many fit; maxRings 4 must still cap the count.
		var sessions = Enumerable.Range(0, 8).Select(i => Sess(0.9 - i * 0.05)).ToArray();
		var r = ContextRingLayout.Build(sessions, 0f, 44f, thickness: 1f, gap: 0f, maxRings: 4);
		Assert.Equal(4, r.Rings.Count);
		Assert.Equal(4, r.Overflow);
	}

	[Fact]
	public void Sweep_clamps_at_full_circle()
	{
		var r = ContextRingLayout.Build(new[] { Sess(1.5) }, 0f, 34f);   // Pct > 1 (tokens exceed window)
		Assert.Equal(360f, r.Rings[0].SweepDegrees, 3);
	}
}
