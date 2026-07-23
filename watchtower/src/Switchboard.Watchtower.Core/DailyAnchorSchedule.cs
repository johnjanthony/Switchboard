namespace Switchboard.Watchtower.Core;

/// Param-clocked gate for the once-a-day session anchor. The caller passes the current local
/// time, the configured anchor time, the grace window, and the date the anchor was last handled.
/// True only when now is within [anchorTime, anchorTime + grace) on a day not yet handled. The
/// narrow window gives "fire only if awake at the anchor, never catch up" for free: a machine
/// asleep across the anchor minute produces no tick inside the window, and a resume lands past it.
public static class DailyAnchorSchedule
{
	public static readonly TimeSpan Grace = TimeSpan.FromMinutes(3);

	public static bool ShouldEvaluate(DateTime nowLocal, TimeOnly anchorTime, TimeSpan grace, DateOnly? handledDate)
	{
		if (handledDate == DateOnly.FromDateTime(nowLocal)) return false;
		var nowTod = nowLocal.TimeOfDay;
		var start = anchorTime.ToTimeSpan();
		return nowTod >= start && nowTod < start + grace;
	}
}
