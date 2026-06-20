using System.Drawing;
using Switchboard.Watchtower.Core;
using Xunit;

public class SeverityGradientTests
{
	[Theory]
	[InlineData(0.0, 63, 185, 80)]    // green
	[InlineData(0.5, 210, 153, 34)]   // amber
	[InlineData(1.0, 248, 81, 73)]    // red
	[InlineData(0.25, 136, 169, 57)]  // midway green->amber
	public void For_interpolates_green_amber_red(double pct, int r, int g, int b)
	{
		Assert.Equal(Color.FromArgb(r, g, b), SeverityGradient.For(pct));
	}
}
