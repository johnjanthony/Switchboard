namespace Switchboard.Watchtower.Core;

public readonly record struct TrayGaugeState(double Max, bool AnyError, Severity MaxSeverity);

/// The tray ring-gauge rule: mirror the busiest non-error session (same rule the
/// widget's % label uses); any error session raises AnyError but does not drive the max.
public static class TrayGauge
{
	public static TrayGaugeState From(IReadOnlyList<SessionModel> sessions)
	{
		bool anyError = false;
		double max = 0;
		Severity maxSev = Severity.Green;
		foreach (var s in sessions)
		{
			anyError |= s.IsError;
			if (!s.IsError && s.Pct >= max) { max = s.Pct; maxSev = s.Severity; }
		}
		return new TrayGaugeState(max, anyError, maxSev);
	}
}
