using System.Text.Json.Serialization;

namespace Switchboard.Watchtower.Core;

// The payload posted to the server's POST /widget-snapshot. Property names are
// pinned to the route contract via [JsonPropertyName] (snake_case), so default
// System.Text.Json serialization produces exactly what the server expects.

public sealed record WidgetRingDto(
	[property: JsonPropertyName("session_id")] string SessionId,
	[property: JsonPropertyName("pct")] double Pct,
	[property: JsonPropertyName("model")] string? Model,
	[property: JsonPropertyName("status")] string Status,
	[property: JsonPropertyName("context_tokens")] long ContextTokens,
	[property: JsonPropertyName("window")] long Window,
	[property: JsonPropertyName("is_error")] bool IsError,
	[property: JsonPropertyName("name")] string? Name,
	[property: JsonPropertyName("name_source")] string? NameSource,
	[property: JsonPropertyName("title_state")] string? TitleState);

public sealed record WidgetQuotaWindowDto(
	[property: JsonPropertyName("pct")] double Pct,
	[property: JsonPropertyName("resets_at")] string? ResetsAt);

public sealed record WidgetQuotaDto(
	[property: JsonPropertyName("session")] WidgetQuotaWindowDto Session,
	[property: JsonPropertyName("weekly")] WidgetQuotaWindowDto Weekly,
	[property: JsonPropertyName("polled_at")] string PolledAt);

public sealed record WidgetSnapshotPayload(
	[property: JsonPropertyName("rings")] IReadOnlyList<WidgetRingDto> Rings,
	[property: JsonPropertyName("quota")] WidgetQuotaDto? Quota,
	[property: JsonPropertyName("pushed_at")] string PushedAt);

public static class WidgetSnapshotBuilder
{
	public static WidgetSnapshotPayload Build(IEnumerable<SessionModel> sessions, QuotaUsage? quota, DateTimeOffset pushedAt,
		IReadOnlyDictionary<string, string>? titleStates = null)
	{
		var rings = new List<WidgetRingDto>();
		foreach (var s in sessions)
		{
			if (string.IsNullOrEmpty(s.SessionId)) continue;  // only rings the server can correlate to a member
			rings.Add(new WidgetRingDto(
				s.SessionId!,
				s.Pct,
				s.Model,
				s.Status == SessionStatus.Live ? "live" : "idle",
				s.ContextTokens,
				s.WindowSize,
				s.IsError,
				s.Name,
				s.NameSource,
				titleStates?.GetValueOrDefault(s.SessionId!)));
		}

		WidgetQuotaDto? quotaDto = null;
		if (quota is QuotaUsage u)
		{
			quotaDto = new WidgetQuotaDto(
				new WidgetQuotaWindowDto(u.Session.Percentage / 100.0, u.Session.ResetsAt?.ToString("o")),
				new WidgetQuotaWindowDto(u.Weekly.Percentage / 100.0, u.Weekly.ResetsAt?.ToString("o")),
				pushedAt.ToString("o"));
		}

		return new WidgetSnapshotPayload(rings, quotaDto, pushedAt.ToString("o"));
	}
}
