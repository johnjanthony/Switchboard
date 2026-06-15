namespace Switchboard.Watchtower.Core;

public static class RelativeTime
{
	/// <summary>
	/// Coarse "N <unit> ago" for a past instant: minutes, hours, then days (singular-aware).
	/// "just now" when under a minute, and a future instant clamps to "just now".
	/// </summary>
	public static string Ago(DateTime pastUtc, DateTime nowUtc)
	{
		double secs = (nowUtc - pastUtc).TotalSeconds;
		if (secs < 60) return "just now";
		long mins = (long)(secs / 60);
		if (mins < 60) return $"{mins} minute{(mins == 1 ? "" : "s")} ago";
		long hours = mins / 60;
		if (hours < 24) return $"{hours} hour{(hours == 1 ? "" : "s")} ago";
		long days = hours / 24;
		return $"{days} day{(days == 1 ? "" : "s")} ago";
	}
}
