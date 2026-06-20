namespace Switchboard.Watchtower.Core;

public static class ActiveClassifier
{
	public static bool IsActive(DateTime mtimeUtc, DateTime nowUtc, int activeWindowMinutes)
	{
		var ageMinutes = (nowUtc - mtimeUtc).TotalMinutes;
		return ageMinutes <= activeWindowMinutes; // negative age (clock skew / future mtime) counts as active
	}

	public static SessionStatus StatusFor(DateTime mtimeUtc, DateTime nowUtc, int liveThresholdSeconds)
	{
		var ageSeconds = (nowUtc - mtimeUtc).TotalSeconds;
		return ageSeconds <= liveThresholdSeconds ? SessionStatus.Live : SessionStatus.Idle;
	}
}
