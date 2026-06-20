using System.Drawing;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal sealed class Palette
{
	public Color Background { get; }
	public Color Surface { get; }
	public Color Text { get; }
	public Color Muted { get; }
	public Color Track { get; }
	public Color Warning { get; }

	public Palette(bool light)
	{
		if (light)
		{
			Background = Color.FromArgb(243, 243, 243);
			Surface = Color.FromArgb(255, 255, 255);
			Text = Color.FromArgb(28, 28, 28);
			Muted = Color.FromArgb(110, 116, 123);
			Track = Color.FromArgb(210, 210, 210);
		}
		else
		{
			Background = Color.FromArgb(31, 31, 31);
			Surface = Color.FromArgb(42, 42, 42);
			Text = Color.FromArgb(237, 237, 237);
			Muted = Color.FromArgb(154, 160, 166);
			Track = Color.FromArgb(58, 58, 58);
		}
		Warning = Color.FromArgb(210, 153, 34);
	}

	public static Color ForSeverity(Severity s) => s switch
	{
		Severity.Red => Color.FromArgb(248, 81, 73),
		Severity.Amber => Color.FromArgb(210, 153, 34),
		_ => Color.FromArgb(63, 185, 80),
	};
}
