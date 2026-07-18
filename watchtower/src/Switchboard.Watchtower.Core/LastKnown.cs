using System.Text.Json;

namespace Switchboard.Watchtower.Core;

// Local render cache: the last data each surface displayed, reloaded at startup so the widget
// and popup render instantly instead of blank until the first scan/poll lands. Deliberately
// named OUTSIDE the WidgetSnapshot vocabulary - that family is the outbound server push; this
// is a private disk cache with a round-trippable, versioned shape.
public sealed class LastKnownState
{
	public int Version { get; set; } = 1;
	public DateTime SavedAtUtc { get; set; }
	public List<LastKnownSession> Sessions { get; set; } = new();
	public DateTime? LastActivityUtc { get; set; }
	public LastKnownQuota? Quota { get; set; }
	public LastKnownStats? Stats { get; set; }
}

public sealed class LastKnownSession
{
	public string Label { get; set; } = "";
	public string? Distro { get; set; }
	public long ContextTokens { get; set; }
	public long WindowSize { get; set; }
	public string? Model { get; set; }
	public string Status { get; set; } = "Idle";
	public DateTime LastActiveUtc { get; set; }
	public bool IsError { get; set; }
	public string? SessionId { get; set; }
	public string? Name { get; set; }
	public string? NameSource { get; set; }
}

public sealed class LastKnownQuota
{
	public double SessionPct { get; set; }
	public DateTimeOffset? SessionResetsAt { get; set; }
	public double WeeklyPct { get; set; }
	public DateTimeOffset? WeeklyResetsAt { get; set; }
}

public sealed class LastKnownStats
{
	public int ActiveConversations { get; set; }
	public int PendingCount { get; set; }
	public double? OldestPendingAgeSeconds { get; set; }
	public bool AwayMode { get; set; }
	public bool Healthy { get; set; }
}

public static class LastKnownStore
{
	static readonly TimeSpan SessionFreshness = TimeSpan.FromMinutes(60);
	static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

	public static string DefaultPath =>
		Path.Combine(Path.GetDirectoryName(AppConfig.DefaultPath)!, "last-known.json");

	// Null on absent, corrupt, or unreadable: a broken cache is worthless - render nothing rather than crash.
	public static LastKnownState? LoadFrom(string path)
	{
		try
		{
			if (!File.Exists(path)) return null;
			return JsonSerializer.Deserialize<LastKnownState>(File.ReadAllText(path), Options);
		}
		catch { return null; }
	}

	// Atomic tmp+move, mirroring AppConfig.SaveTo. False on failure: a cache write must never fault the UI path.
	public static bool SaveTo(LastKnownState state, string path)
	{
		try
		{
			Directory.CreateDirectory(Path.GetDirectoryName(path)!);
			var tmp = path + ".tmp";
			File.WriteAllText(tmp, JsonSerializer.Serialize(state, Options));
			File.Move(tmp, path, overwrite: true);
			return true;
		}
		catch { return false; }
	}

	// Session bars older than this would advertise Live agents that are long gone; quota and
	// stats have no such lie and render at any age (the first live poll replaces them anyway).
	public static bool SessionsFresh(DateTime savedAtUtc, DateTime nowUtc) =>
		nowUtc - savedAtUtc <= SessionFreshness;

	public static LastKnownState From(
		IReadOnlyList<SessionModel> sessions, DateTime? lastActivityUtc,
		QuotaUsage? quota, SwitchboardStats? stats, DateTime savedAtUtc)
	{
		return new LastKnownState
		{
			SavedAtUtc = savedAtUtc,
			LastActivityUtc = lastActivityUtc,
			Sessions = sessions.Select(s => new LastKnownSession
			{
				Label = s.Label,
				Distro = s.Distro,
				ContextTokens = s.ContextTokens,
				WindowSize = s.WindowSize,
				Model = s.Model,
				Status = s.Status.ToString(),
				LastActiveUtc = s.LastActiveUtc,
				IsError = s.IsError,
				SessionId = s.SessionId,
				Name = s.Name,
				NameSource = s.NameSource,
			}).ToList(),
			Quota = quota is QuotaUsage u
				? new LastKnownQuota
				{
					SessionPct = u.Session.Percentage,
					SessionResetsAt = u.Session.ResetsAt,
					WeeklyPct = u.Weekly.Percentage,
					WeeklyResetsAt = u.Weekly.ResetsAt,
				}
				: null,
			Stats = stats is SwitchboardStats st
				? new LastKnownStats
				{
					ActiveConversations = st.ActiveConversations,
					PendingCount = st.PendingCount,
					OldestPendingAgeSeconds = st.OldestPendingAgeSeconds,
					AwayMode = st.AwayMode,
					Healthy = st.Healthy,
				}
				: null,
		};
	}

	public static List<SessionModel> ToSessionModels(LastKnownState state) =>
		state.Sessions.Select(s => new SessionModel(
			s.Label, s.Distro, s.ContextTokens, s.WindowSize, s.Model,
			Enum.TryParse<SessionStatus>(s.Status, out var status) ? status : SessionStatus.Idle,
			s.LastActiveUtc, s.IsError, s.SessionId, s.Name, s.NameSource)).ToList();

	public static QuotaUsage? ToQuota(LastKnownState state) =>
		state.Quota is LastKnownQuota q
			? new QuotaUsage(new QuotaWindow(q.SessionPct, q.SessionResetsAt), new QuotaWindow(q.WeeklyPct, q.WeeklyResetsAt))
			: null;

	public static SwitchboardStats? ToStats(LastKnownState state) =>
		state.Stats is LastKnownStats st
			? new SwitchboardStats(st.ActiveConversations, st.PendingCount, st.OldestPendingAgeSeconds, st.AwayMode, st.Healthy)
			: null;
}
