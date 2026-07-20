using System.Drawing;
using Switchboard.Watchtower.Core;
using Xunit;

public class StatusColorsTests
{
	[Fact]
	public void Constants_match_the_pinned_rgb_triples()
	{
		Assert.Equal(Color.FromArgb(63, 185, 80), StatusColors.Green);
		Assert.Equal(Color.FromArgb(210, 153, 34), StatusColors.Amber);
		Assert.Equal(Color.FromArgb(248, 81, 73), StatusColors.Red);
		Assert.Equal(Color.FromArgb(240, 205, 40), StatusColors.Yellow);
		Assert.Equal(Color.FromArgb(154, 160, 166), StatusColors.Grey);
	}

	[Fact]
	public void Lerp_interpolates_and_clamps()
	{
		var a = Color.FromArgb(0, 0, 0);
		var b = Color.FromArgb(100, 200, 50);
		Assert.Equal(Color.FromArgb(50, 100, 25), StatusColors.Lerp(a, b, 0.5));
		Assert.Equal(a, StatusColors.Lerp(a, b, -1));
		Assert.Equal(b, StatusColors.Lerp(a, b, 2));
	}
}
