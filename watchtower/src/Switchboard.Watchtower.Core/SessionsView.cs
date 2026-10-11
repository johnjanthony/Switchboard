namespace Switchboard.Watchtower.Core;

/// The newest /stats poll, and what the widget, popup and tray last drew. A poll that changes
/// nothing would otherwise re-size and re-position the widget every few seconds, because
/// StatsSessions.ToModels stamps LastActiveUtc with the poll time. Latest always takes the newest
/// poll, since the last-known cache dates each session by its LastActiveUtc.
public sealed class SessionsView
{
	IReadOnlyList<SessionModel>? _drawn;
	bool? _drawnLight;

	public IReadOnlyList<SessionModel> Latest { get; private set; } = Array.Empty<SessionModel>();

	/// Records the poll and says whether it needs drawing: the first poll always does, a taskbar
	/// theme flip does, and otherwise only a change in the sessions themselves.
	public bool Update(IReadOnlyList<SessionModel> sessions, bool light)
	{
		Latest = sessions;
		if (_drawn is not null && _drawnLight == light && SameIgnoringPollTime(_drawn, sessions)) return false;
		_drawn = sessions;
		_drawnLight = light;
		return true;
	}

	static bool SameIgnoringPollTime(IReadOnlyList<SessionModel> a, IReadOnlyList<SessionModel> b) =>
		a.Count == b.Count
		&& a.Zip(b).All(p => p.First with { LastActiveUtc = default } == p.Second with { LastActiveUtc = default });
}
