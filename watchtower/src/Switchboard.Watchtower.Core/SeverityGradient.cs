using System.Drawing;

namespace Switchboard.Watchtower.Core;

/// <summary>
/// Maps a 0..1 fullness to a continuous green -> amber -> red colour (a smooth gradient rather than
/// three discrete severity bands). Used for both the context equalizer bars and the plan-usage bars.
/// </summary>
public static class SeverityGradient
{
	static readonly Color Green = Color.FromArgb(63, 185, 80);
	static readonly Color Amber = Color.FromArgb(210, 153, 34);
	static readonly Color Red = Color.FromArgb(248, 81, 73);

	public static Color For(double pct01)
	{
		double p = Math.Clamp(pct01, 0, 1);
		return p <= 0.5 ? Lerp(Green, Amber, p / 0.5) : Lerp(Amber, Red, (p - 0.5) / 0.5);
	}

	static Color Lerp(Color a, Color b, double t)
	{
		t = Math.Clamp(t, 0, 1);
		return Color.FromArgb(
			(int)(a.R + (b.R - a.R) * t),
			(int)(a.G + (b.G - a.G) * t),
			(int)(a.B + (b.B - a.B) * t));
	}
}
