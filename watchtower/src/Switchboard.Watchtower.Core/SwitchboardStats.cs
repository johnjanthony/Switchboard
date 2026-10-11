using System.Globalization;
using System.Text.Json;

namespace Switchboard.Watchtower.Core;

/// <summary>One needs-you entry from /stats: why the session is blocked on the human and for how long.</summary>
public sealed record NeedsYouEntry(string Reason, double AgeSeconds);

/// <summary>One live session from /stats sessions.items: the server's session record, as the session's mod reports it.</summary>
public sealed record StatsSession(
	string SessionId,
	string? Name,
	string Cwd,
	string Surface,
	string State,
	string? ModelLabel,
	string? Effort,
	long? ContextTokens,
	long? ContextWindow);

/// <summary>
/// The Switchboard server's GET /stats roll-up, as read by the widget. Shape:
/// { "active_conversations": int, "pending_count": int, "oldest_pending_age_seconds": number|null,
///   "away_mode": bool, "healthy": bool,
///   "needs_you": { "<session-id>": { "reason": string, "age_seconds": number } } (optional),
///   "sessions": { ..., "items": [ StatsSession ] } (optional), "quota": { "five_hour": { "percent_used", "resets_at" }, "seven_day": {...}, "received_at" } | null (optional) }.
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

	public static readonly IReadOnlyList<StatsSession> EmptySessions = Array.Empty<StatsSession>();

	/// <summary>Live sessions, newest first. Empty when the server predates the field.</summary>
	public IReadOnlyList<StatsSession> Sessions { get; init; } = EmptySessions;

	/// <summary>The account's plan usage as the server last heard it; null before any reading or on an older server.</summary>
	public QuotaUsage? Quota { get; init; }

	/// <summary>When the server received the quota reading.</summary>
	public DateTimeOffset? QuotaReceivedAt { get; init; }

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

			// sessions.items is optional (absent on older servers -> empty); a present-but-malformed item fails the parse.
			var sessions = EmptySessions;
			if (root.TryGetProperty("sessions", out var sEl) && sEl.ValueKind == JsonValueKind.Object
				&& sEl.TryGetProperty("items", out var itemsEl))
			{
				if (itemsEl.ValueKind != JsonValueKind.Array) return null;
				var list = new List<StatsSession>();
				foreach (var item in itemsEl.EnumerateArray())
				{
					var parsed = ParseSession(item);
					if (parsed is null) return null;
					list.Add(parsed);
				}
				if (list.Count > 0) sessions = list;
			}

			// quota is optional and may be null (no reading yet).
			QuotaUsage? quota = null;
			DateTimeOffset? quotaReceivedAt = null;
			if (root.TryGetProperty("quota", out var qEl) && qEl.ValueKind != JsonValueKind.Null)
			{
				if (qEl.ValueKind != JsonValueKind.Object) return null;
				quota = new QuotaUsage(ReadQuotaWindow(qEl, "five_hour"), ReadQuotaWindow(qEl, "seven_day"));
				quotaReceivedAt = ReadTime(qEl, "received_at");
			}

			return new SwitchboardStats(active, pending, oldestAge, away, healthy) { NeedsYou = needsYou, Sessions = sessions, Quota = quota, QuotaReceivedAt = quotaReceivedAt };
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

	static StatsSession? ParseSession(JsonElement el)
	{
		if (el.ValueKind != JsonValueKind.Object) return null;
		var id = ReadString(el, "session_id");
		var state = ReadString(el, "state");
		if (string.IsNullOrEmpty(id) || string.IsNullOrEmpty(state)) return null;
		return new StatsSession(id, ReadString(el, "name"), ReadString(el, "cwd") ?? "", ReadString(el, "surface") ?? "windows",
			state, ReadString(el, "model_label"), ReadString(el, "effort"), ReadLong(el, "context_tokens"), ReadLong(el, "context_window"));
	}

	// A missing window reads as 0% with no reset, the same as one the server zeroed after its reset.
	static QuotaWindow ReadQuotaWindow(JsonElement quota, string name)
	{
		if (!quota.TryGetProperty(name, out var w) || w.ValueKind != JsonValueKind.Object) return new QuotaWindow(0, null);
		double pct = w.TryGetProperty("percent_used", out var p) && p.ValueKind == JsonValueKind.Number && p.TryGetDouble(out var d) ? d : 0;
		return new QuotaWindow(pct, ReadTime(w, "resets_at"));
	}

	static string? ReadString(JsonElement el, string name) =>
		el.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

	static long? ReadLong(JsonElement el, string name) =>
		el.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n) ? n : null;

	static DateTimeOffset? ReadTime(JsonElement el, string name) =>
		ReadString(el, name) is string s && DateTimeOffset.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var t) ? t : null;
}
