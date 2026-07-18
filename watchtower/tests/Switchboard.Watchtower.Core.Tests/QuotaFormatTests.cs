using Switchboard.Watchtower.Core;
using Xunit;

public class QuotaFormatTests
{
	static readonly DateTimeOffset Now = DateTimeOffset.FromUnixTimeSeconds(1_000_000);

	[Theory]
	[InlineData(11000, 201)]  // 3h2m40s shows "3h"; flips to "2h" in 11000-10800+1
	[InlineData(305, 6)]      // 5m5s shows "5m"; flips to "4m" in 305-300+1
	[InlineData(45, 1)]       // seconds tick every second
	public void TimeUntilDisplayChange_fires_at_next_text_boundary(long deltaSecs, long expectedSecs)
	{
		var t = QuotaFormat.TimeUntilDisplayChange(Now.AddSeconds(deltaSecs), Now);
		Assert.NotNull(t);
		Assert.Equal(expectedSecs, (long)t!.Value.TotalSeconds);
	}

	[Fact]
	public void TimeUntilDisplayChange_null_when_missing_or_past()
	{
		Assert.Null(QuotaFormat.TimeUntilDisplayChange(null, Now));
		Assert.Null(QuotaFormat.TimeUntilDisplayChange(Now.AddSeconds(-5), Now));
	}

	[Theory]
	[InlineData(100, 0, 10, 1.0)]   // fully used -> every segment full
	[InlineData(100, 9, 10, 1.0)]
	[InlineData(0, 0, 10, 0.0)]     // empty
	[InlineData(50, 4, 10, 1.0)]    // segment 4 covers 40-50%; 50 >= end -> full
	[InlineData(50, 5, 10, 0.0)]    // segment 5 covers 50-60%; 50 <= start -> empty
	[InlineData(45, 4, 10, 0.5)]    // segment 4 covers 40-50%; (45-40)/10 -> half
	public void SegmentFill_returns_fraction_per_segment(double pct, int index, int count, double expected)
	{
		Assert.Equal(expected, QuotaFormat.SegmentFill(pct, index, count), 3);
	}

	[Fact]
	public void FormatResetTime_same_day_shows_time_only()
	{
		var now = new DateTimeOffset(new DateTime(2026, 6, 13, 9, 0, 0, DateTimeKind.Local));
		var reset = new DateTimeOffset(new DateTime(2026, 6, 13, 15, 45, 0, DateTimeKind.Local));
		Assert.Equal("3:45 PM", QuotaFormat.FormatResetTime(reset, now));
	}

	[Fact]
	public void FormatResetTime_within_week_shows_day_prefix()
	{
		var now = new DateTimeOffset(new DateTime(2026, 6, 13, 9, 0, 0, DateTimeKind.Local));   // Sat
		var reset = new DateTimeOffset(new DateTime(2026, 6, 15, 15, 45, 0, DateTimeKind.Local)); // Mon
		Assert.Equal("Mon 3:45 PM", QuotaFormat.FormatResetTime(reset, now));
	}

	[Fact]
	public void FormatResetTime_far_out_shows_month_day()
	{
		var now = new DateTimeOffset(new DateTime(2026, 6, 13, 9, 0, 0, DateTimeKind.Local));
		var reset = new DateTimeOffset(new DateTime(2026, 6, 25, 15, 45, 0, DateTimeKind.Local)); // 12 days out
		Assert.Equal("Jun 25, 3:45 PM", QuotaFormat.FormatResetTime(reset, now));
	}

	[Fact]
	public void FormatResetTime_crossing_midnight_shows_next_day()
	{
		var now = new DateTimeOffset(new DateTime(2026, 6, 13, 22, 0, 0, DateTimeKind.Local));   // Sat night
		var reset = new DateTimeOffset(new DateTime(2026, 6, 14, 1, 0, 0, DateTimeKind.Local));  // Sun 1 AM
		Assert.Equal("Sun 1:00 AM", QuotaFormat.FormatResetTime(reset, now));
	}

	[Fact]
	public void FormatResetTime_null_is_empty()
	{
		Assert.Equal("", QuotaFormat.FormatResetTime(null, DateTimeOffset.Now));
	}
}
