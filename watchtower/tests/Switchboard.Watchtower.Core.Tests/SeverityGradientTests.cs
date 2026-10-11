using System.Drawing;
using Switchboard.Watchtower.Core;
using Xunit;

public class SeverityGradientTests
{
	[Theory]
	[InlineData(0.0, 50, 215, 75)]     // green
	[InlineData(0.5, 255, 170, 0)]     // amber
	[InlineData(1.0, 255, 69, 58)]     // red
	[InlineData(0.25, 152, 192, 37)]   // midway green->amber
	public void For_interpolates_green_amber_red(double pct, int r, int g, int b)
	{
		Assert.Equal(Color.FromArgb(r, g, b), SeverityGradient.For(pct));
	}

	[Theory]
	[InlineData(0.10, Severity.Green)]
	[InlineData(0.49, Severity.Green)]
	[InlineData(0.50, Severity.Amber)]
	[InlineData(0.80, Severity.Amber)]
	[InlineData(0.81, Severity.Red)]
	public void SeverityClassifier_uses_50_and_80_bands(double pct, Severity expected)
	{
		Assert.Equal(expected, SeverityClassifier.For(pct));
	}
}
