using Xunit;

namespace Switchboard.Watchtower.Core.Tests;

public class FrostMaterialTests
{
	[Fact]
	public void ApplyFrost_PreservesUniformField_AndAppliesTint()
	{
		int w = 100, h = 100;
		int[] pixels = new int[w * h];
		for (int i = 0; i < pixels.Length; i++)
		{
			pixels[i] = unchecked((int)0xFFFFFFFF); // White
		}

		FrostMaterial.ApplyFrost(pixels, w, h);

		int result = pixels[50 * w + 50];
		int a = (result >> 24) & 0xFF;
		int r = (result >> 16) & 0xFF;
		int g = (result >> 8) & 0xFF;
		int b = result & 0xFF;

		// Math for Color.White (255) tinted with #2A2A2A at alpha 64 (~0.25)
		// (0x2A * 64 + 255 * (255 - 64) + 127) / 255 = 202
		Assert.Equal(255, a);
		Assert.Equal(202, r);
		Assert.Equal(202, g);
		Assert.Equal(202, b);
	}
}

