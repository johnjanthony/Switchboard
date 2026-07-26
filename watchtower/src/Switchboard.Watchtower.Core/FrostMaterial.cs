using System;

namespace Switchboard.Watchtower.Core;

public static class FrostMaterial
{
	public const int BlurRadius = 4;
	public const int TintAlpha = 64;
	// 0x2A2A2A Tint
	public const int TintR = 0x2A;
	public const int TintG = 0x2A;
	public const int TintB = 0x2A;

	public static void ApplyFrost(int[] pixels, int w, int h)
	{
		ApplyFrost(pixels, w, h, BlurRadius, TintR, TintG, TintB, TintAlpha);
	}

	public static void ApplyFrost(int[] pixels, int w, int h, int blurRadius, int tintR, int tintG, int tintB, int tintAlpha)
	{
		int len = w * h;
		int[] tmp = new int[len];

		// Two passes of separable box blur approx Gaussian
		for (int pass = 0; pass < 2; pass++)
		{
			BoxBlurH(pixels, tmp, w, h, blurRadius);
			BoxBlurV(tmp, pixels, w, h, blurRadius);
		}

		ApplyTint(pixels, tintR, tintG, tintB, tintAlpha);
	}

	static void BoxBlurH(int[] src, int[] dst, int w, int h, int r)
	{
		int windowSize = 2 * r + 1;
		for (int y = 0; y < h; y++)
		{
			int rowStart = y * w;
			long sumR = 0, sumG = 0, sumB = 0;

			for (int x = -r; x <= r; x++)
			{
				int px = Math.Clamp(x, 0, w - 1);
				int c = src[rowStart + px];
				sumR += (c >> 16) & 0xFF;
				sumG += (c >> 8) & 0xFF;
				sumB += c & 0xFF;
			}

			for (int x = 0; x < w; x++)
			{
				int avgR = (int)(sumR / windowSize);
				int avgG = (int)(sumG / windowSize);
				int avgB = (int)(sumB / windowSize);
				
				dst[rowStart + x] = unchecked((int)0xFF000000) | (avgR << 16) | (avgG << 8) | avgB;

				int subX = Math.Clamp(x - r, 0, w - 1);
				int addX = Math.Clamp(x + r + 1, 0, w - 1);
				
				int subC = src[rowStart + subX];
				int addC = src[rowStart + addX];

				sumR += ((addC >> 16) & 0xFF) - ((subC >> 16) & 0xFF);
				sumG += ((addC >> 8) & 0xFF) - ((subC >> 8) & 0xFF);
				sumB += (addC & 0xFF) - (subC & 0xFF);
			}
		}
	}

	static void BoxBlurV(int[] src, int[] dst, int w, int h, int r)
	{
		int windowSize = 2 * r + 1;
		for (int x = 0; x < w; x++)
		{
			long sumR = 0, sumG = 0, sumB = 0;

			for (int y = -r; y <= r; y++)
			{
				int py = Math.Clamp(y, 0, h - 1);
				int c = src[py * w + x];
				sumR += (c >> 16) & 0xFF;
				sumG += (c >> 8) & 0xFF;
				sumB += c & 0xFF;
			}

			for (int y = 0; y < h; y++)
			{
				int avgR = (int)(sumR / windowSize);
				int avgG = (int)(sumG / windowSize);
				int avgB = (int)(sumB / windowSize);

				dst[y * w + x] = unchecked((int)0xFF000000) | (avgR << 16) | (avgG << 8) | avgB;

				int subY = Math.Clamp(y - r, 0, h - 1);
				int addY = Math.Clamp(y + r + 1, 0, h - 1);

				int subC = src[subY * w + x];
				int addC = src[addY * w + x];

				sumR += ((addC >> 16) & 0xFF) - ((subC >> 16) & 0xFF);
				sumG += ((addC >> 8) & 0xFF) - ((subC >> 8) & 0xFF);
				sumB += (addC & 0xFF) - (subC & 0xFF);
			}
		}
	}

	static void ApplyTint(int[] pixels, int tr, int tg, int tb, int alpha)
	{
		int invAlpha = 255 - alpha;

		for (int i = 0; i < pixels.Length; i++)
		{
			int c = pixels[i];
			int r = (c >> 16) & 0xFF;
			int g = (c >> 8) & 0xFF;
			int b = c & 0xFF;

			r = (tr * alpha + r * invAlpha + 127) / 255;
			g = (tg * alpha + g * invAlpha + 127) / 255;
			b = (tb * alpha + b * invAlpha + 127) / 255;

			pixels[i] = unchecked((int)0xFF000000) | (r << 16) | (g << 8) | b;
		}
	}
}

