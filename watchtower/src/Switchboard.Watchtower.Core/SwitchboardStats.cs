using System.Text.Json;

namespace Switchboard.Watchtower.Core;

/// <summary>One needs-you entry from /stats: why the session is blocked on the human and for how long.</summary>
public sealed record NeedsYouEntry(string Reason, double AgeSeconds);

/// <summary>
/// The Switchboard server's GET /stats roll-up, as read by the widget. Shape:
/// { "active_conversations": int, "pending_count": int, "oldest_pending_age_seconds": number|null,
///   "away_mode": bool, "healthy": bool,
///   "needs_you": { "<session-id>": { "reason": string, "age_seconds": number } } (optional) }.
/// </summary>
public sealed record SwitchboardStats(
	int ActiveConversations,
	int PendingCount,
	double? OldestPendingAgeSeconds,
	bool AwayMode,
	bool Healthy)
{
	/// <summary>
	/// Shared empty default. Record equality compares dictionary properties by reference, so
	/// every "no entries" stats value must carry this same instance for value equality to hold.
	/// </summary>
	public static readonly IReadOnlyDictionary<string, NeedsYouEntry> EmptyNeedsYou =
		new Dictionary<string, NeedsYouEntry>();

	/// <summary>Sessions blocked on the human, keyed by cli_session_id. Empty when the server predates the field.</summary>
	public IReadOnlyDictionary<string, NeedsYouEntry> NeedsYou { get; init; } = EmptyNeedsYou;

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

			// needs_you is optional (absent on older servers -> empty); present-but-malformed fails the parse.
			var needsYou = EmptyNeedsYou;
			if (root.TryGetProperty("needs_you", out var nyEl))
			{
				if (nyEl.ValueKind != JsonValueKind.Object) return null;
				var map = new Dictionary<string, NeedsYouEntry>();
				foreach (var prop in nyEl.EnumerateObject())
				{
					var v = prop.Value;
					if (v.ValueKind != JsonValueKind.Object) return null;
					if (!v.TryGetProperty("reason", out var rEl) || rEl.ValueKind != JsonValueKind.String) return null;
					if (!v.TryGetProperty("age_seconds", out var aEl) || aEl.ValueKind != JsonValueKind.Number || !aEl.TryGetDouble(out double ageSeconds)) return null;
					map[prop.Name] = new NeedsYouEntry(rEl.GetString()!, ageSeconds);
				}
				if (map.Count > 0) needsYou = map;
			}

			return new SwitchboardStats(active, pending, oldestAge, away, healthy) { NeedsYou = needsYou };
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
