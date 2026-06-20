using System.Text.Json;

namespace Switchboard.Watchtower.Core;

/// <summary>
/// The Switchboard server's GET /stats roll-up, as read by the widget. Shape:
/// { "active_conversations": int, "pending_count": int, "oldest_pending_age_seconds": number|null,
///   "away_mode": bool, "healthy": bool }.
/// </summary>
public sealed record SwitchboardStats(
	int ActiveConversations,
	int PendingCount,
	double? OldestPendingAgeSeconds,
	bool AwayMode,
	bool Healthy)
{
	/// <summary>
	/// Parse the /stats JSON. Returns null on malformed JSON or when any required field is missing or
	/// of the wrong kind. oldest_pending_age_seconds is the only nullable field (null is a valid value).
	/// </summary>
	public static SwitchboardStats? Parse(string json)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			var root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object) return null;

			if (!TryGetInt(root, "active_conversations", out int active)) return null;
			if (!TryGetInt(root, "pending_count", out int pending)) return null;
			if (!TryGetBool(root, "away_mode", out bool away)) return null;
			if (!TryGetBool(root, "healthy", out bool healthy)) return null;

			// oldest_pending_age_seconds is required to be present, but its value may be null.
			if (!root.TryGetProperty("oldest_pending_age_seconds", out var ageEl)) return null;
			double? oldestAge;
			if (ageEl.ValueKind == JsonValueKind.Null) oldestAge = null;
			else if (ageEl.ValueKind == JsonValueKind.Number && ageEl.TryGetDouble(out double age)) oldestAge = age;
			else return null;

			return new SwitchboardStats(active, pending, oldestAge, away, healthy);
		}
		catch (JsonException) { return null; }
	}

	static bool TryGetInt(JsonElement root, string name, out int value)
	{
		value = 0;
		return root.TryGetProperty(name, out var el)
			&& el.ValueKind == JsonValueKind.Number
			&& el.TryGetInt32(out value);
	}

	static bool TryGetBool(JsonElement root, string name, out bool value)
	{
		value = false;
		if (!root.TryGetProperty(name, out var el)) return false;
		if (el.ValueKind == JsonValueKind.True) { value = true; return true; }
		if (el.ValueKind == JsonValueKind.False) { value = false; return true; }
		return false;
	}
}
