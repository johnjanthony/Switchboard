using System.Drawing;

namespace Switchboard.Watchtower.Core;

// The shared status palette: one home for the RGB triples that repeat across the severity
// gradient, widget rings, popup, and tray. System.Drawing.Color here comes from
// System.Drawing.Primitives (shared framework, cross-platform) - no WinForms dependency.
public static class StatusColors
{
	public static readonly Color Green = Color.FromArgb(63, 185, 80);    // ok / live / low usage
	public static readonly Color Amber = Color.FromArgb(210, 153, 34);   // warning / pending badge / gradient knee
	public static readonly Color Red = Color.FromArgb(248, 81, 73);      // error / critical / high usage
	public static readonly Color Yellow = Color.FromArgb(240, 205, 40);  // ring midpoint / minor incident
	public static readonly Color Grey = Color.FromArgb(154, 160, 166);   // muted / unknown

	public static Color Lerp(Color a, Color b, double t)
	{
		t = Math.Clamp(t, 0, 1);
		return Color.FromArgb(
			(int)(a.R + (b.R - a.R) * t),
			(int)(a.G + (b.G - a.G) * t),
			(int)(a.B + (b.B - a.B) * t));
	}
}
