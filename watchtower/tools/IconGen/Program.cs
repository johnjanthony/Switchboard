using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

// Generates the app .ico: a ring gauge (a ~72% arc) in the same style as the tray indicator and the
// VS Code Claude Code context indicator. Multi-resolution, PNG-encoded entries (Windows Vista+).

string outPath = args.Length > 0 ? args[0] : "icon.ico";
int[] sizes = { 16, 24, 32, 48, 64, 128, 256 };

var arcColor = Color.FromArgb(255, 217, 119, 87);   // Claude coral/orange
var trackColor = Color.FromArgb(90, 140, 140, 140); // faint, reads on light and dark backgrounds

var pngs = new List<byte[]>();
foreach (int s in sizes)
{
	using var bmp = new Bitmap(s, s, PixelFormat.Format32bppArgb);
	using (var g = Graphics.FromImage(bmp))
	{
		g.SmoothingMode = SmoothingMode.AntiAlias;
		g.Clear(Color.Transparent);

		float thickness = MathF.Max(2f, s * 0.16f);
		float inset = thickness / 2f + s * 0.08f;
		var ring = new RectangleF(inset, inset, s - 2 * inset, s - 2 * inset);

		using (var track = new Pen(trackColor, thickness))
			g.DrawEllipse(track, ring);
		using (var arc = new Pen(arcColor, thickness) { StartCap = LineCap.Round, EndCap = LineCap.Round })
			g.DrawArc(arc, ring, -90f, 360f * 0.72f);   // start at 12 o'clock, fill clockwise
	}

	using var ms = new MemoryStream();
	bmp.Save(ms, ImageFormat.Png);
	pngs.Add(ms.ToArray());
}

using var fs = new FileStream(outPath, FileMode.Create);
using var bw = new BinaryWriter(fs);
bw.Write((ushort)0);              // reserved
bw.Write((ushort)1);              // type = icon
bw.Write((ushort)sizes.Length);   // image count
int offset = 6 + 16 * sizes.Length;
for (int i = 0; i < sizes.Length; i++)
{
	int sz = sizes[i];
	bw.Write((byte)(sz >= 256 ? 0 : sz));  // width  (0 means 256)
	bw.Write((byte)(sz >= 256 ? 0 : sz));  // height (0 means 256)
	bw.Write((byte)0);                     // palette
	bw.Write((byte)0);                     // reserved
	bw.Write((ushort)1);                   // color planes
	bw.Write((ushort)32);                  // bits per pixel
	bw.Write((uint)pngs[i].Length);        // size of image data
	bw.Write((uint)offset);                // offset of image data
	offset += pngs[i].Length;
}
foreach (byte[] p in pngs) bw.Write(p);

Console.WriteLine($"Wrote {outPath}: {sizes.Length} sizes, {offset} bytes total");
