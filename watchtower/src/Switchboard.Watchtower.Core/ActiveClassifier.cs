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

	// A needs-you session's transcript is listed regardless of transcript age; the id set
	// (cli_session_id == transcript filename stem) comes from the server's /stats needs_you map.
	public static bool IsRetained(string path, IReadOnlySet<string>? retainIds) =>
		retainIds is { Count: > 0 } && retainIds.Contains(Path.GetFileNameWithoutExtension(path));
}
