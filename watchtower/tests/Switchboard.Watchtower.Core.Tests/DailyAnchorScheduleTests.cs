using Switchboard.Watchtower.Core;
using Xunit;

public class DailyAnchorScheduleTests
{
	static readonly TimeOnly Anchor = new(7, 0);
	static readonly TimeSpan Grace = TimeSpan.FromMinutes(3);

	[Fact]
	public void Before_the_window_does_not_evaluate()
	{
		var now = new DateTime(2026, 7, 22, 6, 59, 0);
		Assert.False(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, null));
	}

	[Fact]
	public void First_tick_inside_the_window_evaluates()
	{
		var now = new DateTime(2026, 7, 22, 7, 0, 0);
		Assert.True(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, null));
	}

	[Fact]
	public void Late_tick_still_inside_the_window_evaluates()
	{
		var now = new DateTime(2026, 7, 22, 7, 2, 59);
		Assert.True(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, null));
	}

	[Fact]
	public void At_the_end_of_the_window_does_not_evaluate()
	{
		var now = new DateTime(2026, 7, 22, 7, 3, 0);
		Assert.False(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, null));
	}

	[Fact]
	public void Already_handled_today_does_not_evaluate()
	{
		var now = new DateTime(2026, 7, 22, 7, 1, 0);
		var handledToday = new DateOnly(2026, 7, 22);
		Assert.False(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, handledToday));
	}

	[Fact]
	public void Resume_after_the_window_does_not_evaluate()
	{
		// Asleep across 07:00, resumed at 07:10: no tick ever landed in the window.
		var now = new DateTime(2026, 7, 22, 7, 10, 0);
		Assert.False(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, null));
	}

	[Fact]
	public void Handled_yesterday_evaluates_again_today()
	{
		var now = new DateTime(2026, 7, 22, 7, 1, 0);
		var handledYesterday = new DateOnly(2026, 7, 21);
		Assert.True(DailyAnchorSchedule.ShouldEvaluate(now, Anchor, Grace, handledYesterday));
	}

	[Fact]
	public void Grace_is_three_minutes()
	{
		Assert.Equal(TimeSpan.FromMinutes(3), DailyAnchorSchedule.Grace);
	}

	static readonly DateTimeOffset AnchorNow = new(2026, 10, 10, 7, 0, 0, TimeSpan.Zero);

	static QuotaUsage Session(double pct, DateTimeOffset? resetsAt) => new(new QuotaWindow(pct, resetsAt), new QuotaWindow(0, null));

	[Fact]
	public void A_future_reset_with_usage_is_an_open_window() =>
		Assert.True(DailyAnchorSchedule.WindowAlreadyOpen(Session(12, AnchorNow.AddHours(3)), AnchorNow));

	[Fact]
	public void A_future_reset_with_no_usage_is_not_an_open_window() =>
		Assert.False(DailyAnchorSchedule.WindowAlreadyOpen(Session(0, AnchorNow.AddHours(3)), AnchorNow));

	[Fact]
	public void A_passed_reset_is_not_an_open_window() =>
		Assert.False(DailyAnchorSchedule.WindowAlreadyOpen(Session(12, AnchorNow.AddHours(-1)), AnchorNow));

	[Fact]
	public void No_reading_or_no_reset_means_fire()
	{
		Assert.False(DailyAnchorSchedule.WindowAlreadyOpen(null, AnchorNow));
		Assert.False(DailyAnchorSchedule.WindowAlreadyOpen(Session(12, null), AnchorNow));
	}
}
