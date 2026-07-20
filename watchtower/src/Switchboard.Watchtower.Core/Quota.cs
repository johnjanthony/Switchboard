using System.Globalization;
using System.Text.Json;

namespace Switchboard.Watchtower.Core;

/// <summary>One usage window: percentage used (0-100) and when it resets.</summary>
public readonly record struct QuotaWindow(double Percentage, DateTimeOffset? ResetsAt);

/// <summary>Claude plan usage: the rolling 5-hour (session) and 7-day (weekly) windows.</summary>
public readonly record struct QuotaUsage(QuotaWindow Session, QuotaWindow Weekly);

/// <summary>Whether usage is running ahead of, behind, or on the elapsed-time pace; Unknown if no reset time.</summary>
public enum PaceVerdict { Unknown, Under, OnPace, Over }

/// <summary>Pace for one window: how far through the time window we are (0..1, null if unknown) and the verdict.</summary>
public readonly record struct PaceInfo(double? ElapsedFraction, PaceVerdict Verdict);

/// <summary>
/// Compares how much of a usage window has been consumed against how much of its time has elapsed.
/// The window runs from (ResetsAt - duration) to ResetsAt, so elapsed fraction = (now - start) / duration.
/// </summary>
public static class QuotaPacing
{
	public static readonly TimeSpan SessionDuration = TimeSpan.FromHours(5);
	public static readonly TimeSpan WeeklyDuration = TimeSpan.FromDays(7);

	// Tolerance band (in 0..1 fraction) within which usage is treated as "on pace" rather than over/under.
	const double Epsilon = 0.02;

	public static PaceInfo Compute(QuotaWindow w, TimeSpan duration, DateTimeOffset now)
	{
		if (w.ResetsAt is not DateTimeOffset reset)
			return new PaceInfo(null, PaceVerdict.Unknown);

		DateTimeOffset start = reset - duration;
		double elapsed = Math.Clamp((now - start) / duration, 0, 1);
		double usage = Math.Clamp(w.Percentage / 100.0, 0, 1);
		PaceVerdict v = usage > elapsed + Epsilon ? PaceVerdict.Over
			: usage < elapsed - Epsilon ? PaceVerdict.Under
			: PaceVerdict.OnPace;
		return new PaceInfo(elapsed, v);
	}
}

public static class QuotaParser
{
	/// <summary>
	/// Parse the response of GET /api/oauth/usage:
	/// { "five_hour": { "utilization": 50, "resets_at": "..." }, "seven_day": { ... } }.
	/// Returns null if the JSON is malformed. utilization is already a percentage (0-100).
	/// </summary>
	public static QuotaUsage? ParseUsage(string json)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			var root = doc.RootElement;
			return new QuotaUsage(ReadWindow(root, "five_hour"), ReadWindow(root, "seven_day"));
		}
		catch (JsonException) { return null; }
	}

	static QuotaWindow ReadWindow(JsonElement root, string name)
	{
		if (!root.TryGetProperty(name, out var w) || w.ValueKind != JsonValueKind.Object)
			return default;
		double pct = w.TryGetProperty("utilization", out var u) && u.TryGetDouble(out var d) ? d : 0;
		DateTimeOffset? reset = null;
		if (w.TryGetProperty("resets_at", out var r) && r.ValueKind == JsonValueKind.String
			&& DateTimeOffset.TryParse(r.GetString(), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var dto))
			reset = dto;
		return new QuotaWindow(pct, reset);
	}

	/// <summary>Parse ~/.claude/.credentials.json: claudeAiOauth.accessToken + expiresAt (ms epoch).</summary>
	public static (string Token, long? ExpiresAtMs)? ParseCredentials(string json)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			if (!doc.RootElement.TryGetProperty("claudeAiOauth", out var oauth) || oauth.ValueKind != JsonValueKind.Object)
				return null;
			if (!oauth.TryGetProperty("accessToken", out var t) || t.ValueKind != JsonValueKind.String)
				return null;
			string? token = t.GetString();
			if (string.IsNullOrEmpty(token)) return null;
			long? exp = oauth.TryGetProperty("expiresAt", out var e) && e.TryGetInt64(out var ms) ? ms : null;
			return (token, exp);
		}
		catch (JsonException) { return null; }
	}

	/// <summary>True if the OAuth token's expiry (ms epoch) is at or before <paramref name="now"/>.</summary>
	public static bool IsExpired(long? expiresAtMs, DateTimeOffset now)
		=> expiresAtMs is long exp && now.ToUnixTimeMilliseconds() >= exp;
}

public static class QuotaFormat
{
	/// <summary>How long until the countdown text ("3d"/"3h"/"45m"/"30s") would next change; null if unknown or already past.</summary>
	public static TimeSpan? TimeUntilDisplayChange(DateTimeOffset? resetsAt, DateTimeOffset now)
	{
		if (resetsAt is not DateTimeOffset reset) return null;
		var remaining = reset - now;
		if (remaining <= TimeSpan.Zero) return null;
		long secs = (long)remaining.TotalSeconds;
		long days = secs / 86400, hours = secs / 3600, mins = secs / 60;
		long bucketStart = days >= 1 ? days * 86400 : hours >= 1 ? hours * 3600 : mins >= 1 ? mins * 60 : secs;
		return TimeSpan.FromSeconds(secs - bucketStart + 1);
	}

	/// <summary>
	/// Exact 12-hour reset time in local wall-clock: same day -> "3:45 PM"; within the next 6 days ->
	/// "Fri 3:45 PM"; further out -> "Jun 19, 3:45 PM". Empty string when the reset time is unknown.
	/// </summary>
	public static string FormatResetTime(DateTimeOffset? resetsAt, DateTimeOffset now)
	{
		if (resetsAt is not DateTimeOffset r) return "";
		DateTime reset = r.ToLocalTime().DateTime;
		DateTime today = now.ToLocalTime().DateTime;
		string time = reset.ToString("h:mm tt", CultureInfo.InvariantCulture);
		int dayDiff = (reset.Date - today.Date).Days;
		if (dayDiff <= 0) return time;                                                       // today (or already past)
		if (dayDiff <= 6) return $"{reset.ToString("ddd", CultureInfo.InvariantCulture)} {time}";
		return $"{reset.ToString("MMM d", CultureInfo.InvariantCulture)}, {time}";
	}

	/// <summary>Fill fraction (0..1) of segment <paramref name="index"/> of <paramref name="count"/> for a 0-100 percentage.</summary>
	public static double SegmentFill(double percentage, int index, int count)
	{
		double p = Math.Clamp(percentage, 0, 100);
		double segPct = 100.0 / count;
		double segStart = index * segPct;
		double segEnd = segStart + segPct;
		if (p >= segEnd) return 1.0;
		if (p <= segStart) return 0.0;
		return (p - segStart) / segPct;
	}
}
