using System.Drawing;

namespace Switchboard.Watchtower.Core;

/// <summary>
/// Maps a 0..1 fullness to a continuous green -> amber -> red colour (a smooth gradient rather than
/// three discrete severity bands). Used for both the context rings and the plan-usage bars.
/// </summary>
public static class SeverityGradient
{
	public static Color For(double pct01)
	{
		double p = Math.Clamp(pct01, 0, 1);
		return p <= 0.5
			? StatusColors.Lerp(StatusColors.Green, StatusColors.Amber, p / 0.5)
			: StatusColors.Lerp(StatusColors.Amber, StatusColors.Red, (p - 0.5) / 0.5);
	}
}
