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
}
