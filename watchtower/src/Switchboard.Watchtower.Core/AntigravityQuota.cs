using System.Globalization;
using System.Text.Json;

namespace Switchboard.Watchtower.Core;

public sealed record AntigravityQuotaBucket(string Window, double RemainingFraction, DateTimeOffset? ResetTimeUtc);

public sealed record AntigravityQuotaGroup(string DisplayName, string? Description, IReadOnlyList<AntigravityQuotaBucket> Buckets);

public sealed record AntigravityQuotaSummary(IReadOnlyList<AntigravityQuotaGroup> Groups);

public static class AntigravityQuota
{
	/// <summary>Parse the RetrieveUserQuotaSummary response; null if malformed or missing the expected shape.</summary>
	public static AntigravityQuotaSummary? Parse(string json)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			if (!doc.RootElement.TryGetProperty("response", out var resp) || resp.ValueKind != JsonValueKind.Object)
				return null;
			var groups = new List<AntigravityQuotaGroup>();
			if (resp.TryGetProperty("groups", out var gs) && gs.ValueKind == JsonValueKind.Array)
			{
				foreach (var g in gs.EnumerateArray())
				{
					string display = g.TryGetProperty("displayName", out var dn) && dn.ValueKind == JsonValueKind.String ? dn.GetString()! : "";
					string? desc = g.TryGetProperty("description", out var de) && de.ValueKind == JsonValueKind.String ? de.GetString() : null;
					var buckets = new List<AntigravityQuotaBucket>();
					if (g.TryGetProperty("buckets", out var bs) && bs.ValueKind == JsonValueKind.Array)
						foreach (var b in bs.EnumerateArray())
							buckets.Add(ReadBucket(b));
					groups.Add(new AntigravityQuotaGroup(display, desc, buckets));
				}
			}
			return new AntigravityQuotaSummary(groups);
		}
		catch (JsonException) { return null; }
	}

	static AntigravityQuotaBucket ReadBucket(JsonElement b)
	{
		string window = b.TryGetProperty("window", out var w) && w.ValueKind == JsonValueKind.String ? w.GetString()! : "";
		double remaining = 0;
		if (b.TryGetProperty("remainingFraction", out var rf))
		{
			if (rf.ValueKind == JsonValueKind.String)
				double.TryParse(rf.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out remaining);
			else if (rf.TryGetDouble(out var d))
				remaining = d;
		}
		DateTimeOffset? reset = null;
		if (b.TryGetProperty("resetTime", out var rt) && rt.ValueKind == JsonValueKind.String
			&& DateTimeOffset.TryParse(rt.GetString(), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var dto))
			reset = dto;
		return new AntigravityQuotaBucket(window, remaining, reset);
	}

	/// <summary>Used percentage (0-100) = inverse of remaining, clamped.</summary>
	public static double UsedPercent(AntigravityQuotaBucket b) => Math.Clamp((1.0 - b.RemainingFraction) * 100.0, 0, 100);

	/// <summary>A group is visible when any bucket has been consumed at all (used &gt; 0).</summary>
	public static bool IsGroupVisible(AntigravityQuotaGroup g) => g.Buckets.Any(b => UsedPercent(b) > 0);

	/// <summary>Finds the bucket for a window ("5h" / "weekly"); null if absent.</summary>
	public static AntigravityQuotaBucket? Bucket(AntigravityQuotaGroup g, string window)
		=> g.Buckets.FirstOrDefault(b => string.Equals(b.Window, window, StringComparison.OrdinalIgnoreCase));

	/// <summary>Convert a group's bucket to the used-framing QuotaWindow the bar renderers expect (used = 1 - remaining); (0, null) when the bucket is absent.</summary>
	public static QuotaWindow ToUsedWindow(AntigravityQuotaGroup group, string window)
	{
		var b = Bucket(group, window);
		return b is null ? new QuotaWindow(0, null) : new QuotaWindow(UsedPercent(b), b.ResetTimeUtc);
	}

	/// <summary>Shared display order for agy groups (widget left-to-right and popup top-to-bottom): Claude-family first, then Gemini, then any other.</summary>
	public static int GroupSortKey(AntigravityQuotaGroup group)
	{
		string d = group.DisplayName ?? "";
		if (d.Contains("Claude", StringComparison.OrdinalIgnoreCase)) return 0;
		if (d.Contains("Gemini", StringComparison.OrdinalIgnoreCase)) return 1;
		return 2;
	}
}
