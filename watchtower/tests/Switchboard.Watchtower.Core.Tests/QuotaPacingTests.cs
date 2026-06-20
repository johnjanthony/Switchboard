using Switchboard.Watchtower.Core;
using Xunit;

public class QuotaPacingTests
{
	static readonly DateTimeOffset Now = DateTimeOffset.FromUnixTimeSeconds(1_000_000);
	static readonly TimeSpan FiveHours = TimeSpan.FromHours(5);

	static QuotaWindow Window(double pct, DateTimeOffset? reset) => new(pct, reset);

	[Fact]
	public void Over_pace_when_usage_exceeds_elapsed()
	{
		// reset is 170m out -> window started 130m ago -> elapsed 130/300 ~= 43%; used 62% -> Over
		var reset = Now.Add(FiveHours - TimeSpan.FromMinutes(130));
		var p = QuotaPacing.Compute(Window(62, reset), FiveHours, Now);
		Assert.Equal(PaceVerdict.Over, p.Verdict);
		Assert.Equal(130.0 / 300.0, p.ElapsedFraction!.Value, 3);
	}

	[Fact]
	public void Under_pace_when_usage_below_elapsed()
	{
		var reset = Now.Add(FiveHours - TimeSpan.FromMinutes(150)); // elapsed 50%
		var p = QuotaPacing.Compute(Window(38, reset), FiveHours, Now);
		Assert.Equal(PaceVerdict.Under, p.Verdict);
	}

	[Fact]
	public void On_pace_when_usage_matches_elapsed()
	{
		var reset = Now.Add(FiveHours - TimeSpan.FromMinutes(150)); // elapsed 50%
		var p = QuotaPacing.Compute(Window(50, reset), FiveHours, Now);
		Assert.Equal(PaceVerdict.OnPace, p.Verdict);
	}

	[Fact]
	public void Unknown_when_no_reset()
	{
		var p = QuotaPacing.Compute(Window(50, null), FiveHours, Now);
		Assert.Null(p.ElapsedFraction);
		Assert.Equal(PaceVerdict.Unknown, p.Verdict);
	}

	[Fact]
	public void Elapsed_clamps_to_one_when_reset_just_passed()
	{
		var reset = Now.AddMinutes(-1); // window already over
		var p = QuotaPacing.Compute(Window(80, reset), FiveHours, Now);
		Assert.Equal(1.0, p.ElapsedFraction!.Value, 3);
	}

	[Fact]
	public void Elapsed_clamps_to_zero_before_window_start()
	{
		// reset more than a full duration away -> window not started yet
		var reset = Now.Add(FiveHours + TimeSpan.FromHours(1));
		var p = QuotaPacing.Compute(Window(0, reset), FiveHours, Now);
		Assert.Equal(0.0, p.ElapsedFraction!.Value, 3);
	}
}
