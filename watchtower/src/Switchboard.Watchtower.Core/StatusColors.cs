using System.Drawing;

namespace Switchboard.Watchtower.Core;

// The shared status palette: one home for the RGB triples that repeat across the severity
// gradient, widget rings, popup, and tray. System.Drawing.Color here comes from
// System.Drawing.Primitives (shared framework, cross-platform) - no WinForms dependency.
public static class StatusColors
{
	public static readonly Color Green = Color.FromArgb(50, 215, 75);     // ok / live / low usage (vivid green)
	public static readonly Color Amber = Color.FromArgb(255, 170, 0);    // warning / pending badge / gradient knee (vivid amber)
	public static readonly Color Red = Color.FromArgb(255, 69, 58);      // error / critical / high usage (vivid red)
	public static readonly Color Yellow = Color.FromArgb(255, 214, 10);   // ring midpoint / minor incident (vivid yellow)
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
