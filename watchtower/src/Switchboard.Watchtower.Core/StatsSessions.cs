namespace Switchboard.Watchtower.Core;

/// Turns the server's live sessions (/stats sessions.items) into the SessionModel the widget, popup
/// and tray draw. The server owns liveness and context now, so nothing here reads a transcript.
public static class StatsSessions
{
	static readonly HashSet<string> LiveStates = new() { "active", "awaiting_human", "awaiting_agent" };

	public static List<SessionModel> ToModels(IReadOnlyList<StatsSession> items, DateTime nowUtc) =>
		items.Select(s => new SessionModel(
			Label: CwdLabeler.Label(s.Cwd),
			Distro: s.Surface == "wsl" ? "WSL" : null,
			ContextTokens: s.ContextTokens ?? 0,
			WindowSize: s.ContextWindow ?? 0,
			Model: s.ModelLabel,
			Status: LiveStates.Contains(s.State) ? SessionStatus.Live : SessionStatus.Idle,
			LastActiveUtc: nowUtc,
			SessionId: s.SessionId,
			Name: s.Name,
			Effort: s.Effort)).ToList();
}
