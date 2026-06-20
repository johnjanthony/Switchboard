using Switchboard.Watchtower.Core;
using Xunit;

public class ActiveClassifierTests
{
	static readonly DateTime Now = new(2026, 6, 12, 12, 0, 0, DateTimeKind.Utc);

	[Theory]
	[InlineData(30, true)]    // 30s ago
	[InlineData(299, true)]   // ~5 min ago, within window
	[InlineData(600, false)]  // 10 min ago, stale
	public void IsActive_uses_window_minutes(int secondsAgo, bool expected)
	{
		var mtime = Now.AddSeconds(-secondsAgo);
		Assert.Equal(expected, ActiveClassifier.IsActive(mtime, Now, activeWindowMinutes: 5));
	}

	[Theory]
	[InlineData(30, SessionStatus.Live)]
	[InlineData(90, SessionStatus.Live)]
	[InlineData(120, SessionStatus.Idle)]
	public void StatusFor_uses_live_threshold_seconds(int secondsAgo, SessionStatus expected)
	{
		var mtime = Now.AddSeconds(-secondsAgo);
		Assert.Equal(expected, ActiveClassifier.StatusFor(mtime, Now, liveThresholdSeconds: 90));
	}
}
