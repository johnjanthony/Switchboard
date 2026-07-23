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

public sealed record WidgetQuotaGroupDto(
	[property: JsonPropertyName("display_name")] string DisplayName,
	[property: JsonPropertyName("session")] WidgetQuotaWindowDto Session,
	[property: JsonPropertyName("weekly")] WidgetQuotaWindowDto Weekly);

public sealed record WidgetQuotaDto(
	[property: JsonPropertyName("session")] WidgetQuotaWindowDto Session,
	[property: JsonPropertyName("weekly")] WidgetQuotaWindowDto Weekly,
	[property: JsonPropertyName("polled_at")] string PolledAt,
	[property: JsonPropertyName("antigravity")] IReadOnlyList<WidgetQuotaGroupDto>? Antigravity = null);

public sealed record WidgetSnapshotPayload(
	[property: JsonPropertyName("rings")] IReadOnlyList<WidgetRingDto> Rings,
	[property: JsonPropertyName("quota")] WidgetQuotaDto? Quota,
	[property: JsonPropertyName("pushed_at")] string PushedAt);

public static class WidgetSnapshotBuilder
{
	public static WidgetSnapshotPayload Build(IEnumerable<SessionModel> sessions, QuotaUsage? quota, DateTimeOffset pushedAt,
		IReadOnlyDictionary<string, string>? titleStates = null, IReadOnlyList<AntigravityQuotaGroup>? agyGroups = null)
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

		List<WidgetQuotaGroupDto>? agyDtos = null;
		if (agyGroups is not null && agyGroups.Count > 0)
		{
			var visible = agyGroups.Where(AntigravityQuota.IsGroupVisible).OrderBy(AntigravityQuota.GroupSortKey).ToList();
			if (visible.Count > 0)
			{
				agyDtos = new List<WidgetQuotaGroupDto>();
				foreach (var g in visible)
				{
					var s = AntigravityQuota.ToUsedWindow(g, "5h");
					var w = AntigravityQuota.ToUsedWindow(g, "weekly");
					agyDtos.Add(new WidgetQuotaGroupDto(
						g.DisplayName,
						new WidgetQuotaWindowDto(s.Percentage / 100.0, s.ResetsAt?.ToString("o")),
						new WidgetQuotaWindowDto(w.Percentage / 100.0, w.ResetsAt?.ToString("o"))));
				}
			}
		}

		WidgetQuotaDto? quotaDto = null;
		if (quota is QuotaUsage u)
		{
			quotaDto = new WidgetQuotaDto(
				new WidgetQuotaWindowDto(u.Session.Percentage / 100.0, u.Session.ResetsAt?.ToString("o")),
				new WidgetQuotaWindowDto(u.Weekly.Percentage / 100.0, u.Weekly.ResetsAt?.ToString("o")),
				pushedAt.ToString("o"),
				agyDtos);
		}
		else if (agyDtos is not null && agyDtos.Count > 0)
		{
			quotaDto = new WidgetQuotaDto(
				new WidgetQuotaWindowDto(0, null),
				new WidgetQuotaWindowDto(0, null),
				pushedAt.ToString("o"),
				agyDtos);
		}

		return new WidgetSnapshotPayload(rings, quotaDto, pushedAt.ToString("o"));
	}
}
