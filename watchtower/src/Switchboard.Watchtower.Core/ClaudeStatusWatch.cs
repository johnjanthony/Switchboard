namespace Switchboard.Watchtower.Core;

public enum ClaudeWatchState { Idle, Watching, ResolvedUnacked, CappedUnacked }

public enum ClaudeStatusButton { CheckNow, StopWatching, Clear }

public enum WatchAction { None, StartPolling, StopPolling }

/// <summary>The derived view the display surfaces render. Pure projection of watch state.</summary>
public sealed record ClaudeStatusView(
	bool DotVisible,
	ClaudeStatusLevel DotLevel,
	bool HasData,
	string Description,
	IReadOnlyList<string> IncidentNames,
	DateTime? FetchedAtUtc,
	ClaudeStatusButton Button);

/// <summary>
/// The manual watch-until-resolved state machine. Pure (no I/O): the host owns the
/// timer and the HTTP fetch, and feeds results in via ApplyFetch. The dot stays
/// visible from the moment a watch begins until Acknowledge, independent of whether
/// polling is still running (which stops on Operational or the safety cap).
/// </summary>
public sealed class ClaudeStatusWatch
{
	readonly int _maxWatchMinutes;
	ClaudeWatchState _state = ClaudeWatchState.Idle;
	ClaudeStatus? _last;
	DateTime? _watchStartUtc;

	public ClaudeStatusWatch(int maxWatchMinutes = 180) => _maxWatchMinutes = Math.Max(1, maxWatchMinutes);

	public ClaudeWatchState State => _state;

	public WatchAction ApplyFetch(ClaudeStatus status, DateTime nowUtc)
	{
		_last = status;
		switch (_state)
		{
			case ClaudeWatchState.Idle:
				if (status.Level is ClaudeStatusLevel.Minor or ClaudeStatusLevel.Major or ClaudeStatusLevel.Critical)
				{
					_state = ClaudeWatchState.Watching;
					_watchStartUtc = nowUtc;
					return WatchAction.StartPolling;
				}
				return WatchAction.None;

			case ClaudeWatchState.Watching:
				if (status.Level == ClaudeStatusLevel.Operational)
				{
					_state = ClaudeWatchState.ResolvedUnacked;
					return WatchAction.StopPolling;
				}
				if (_watchStartUtc is DateTime start && nowUtc - start >= TimeSpan.FromMinutes(_maxWatchMinutes))
				{
					_state = ClaudeWatchState.CappedUnacked;
					return WatchAction.StopPolling;
				}
				return WatchAction.None;

			default:
				return WatchAction.None;
		}
	}

	public WatchAction Acknowledge()
	{
		if (_state == ClaudeWatchState.Idle) return WatchAction.None;
		_state = ClaudeWatchState.Idle;
		_watchStartUtc = null;
		return WatchAction.StopPolling;
	}

	public ClaudeStatusView Snapshot()
	{
		var dotLevel = _state switch
		{
			ClaudeWatchState.ResolvedUnacked => ClaudeStatusLevel.Operational,
			ClaudeWatchState.Watching => _last?.Level ?? ClaudeStatusLevel.Unknown,
			ClaudeWatchState.CappedUnacked => _last?.Level ?? ClaudeStatusLevel.Unknown,
			_ => ClaudeStatusLevel.Operational,
		};
		var button = _state switch
		{
			ClaudeWatchState.Watching => ClaudeStatusButton.StopWatching,
			ClaudeWatchState.ResolvedUnacked or ClaudeWatchState.CappedUnacked => ClaudeStatusButton.Clear,
			_ => ClaudeStatusButton.CheckNow,
		};
		return new ClaudeStatusView(
			DotVisible: _state != ClaudeWatchState.Idle,
			DotLevel: dotLevel,
			HasData: _last is not null,
			Description: _last?.Description ?? "",
			IncidentNames: _last?.IncidentNames ?? Array.Empty<string>(),
			FetchedAtUtc: _last?.FetchedAtUtc,
			Button: button);
	}
}
