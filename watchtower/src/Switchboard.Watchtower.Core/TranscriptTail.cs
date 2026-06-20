namespace Switchboard.Watchtower.Core;

public static class TranscriptTail
{
	// Returns the last line that parses as an assistant turn. Opened shared so a live Claude process
	// can keep writing. An actively-working session can have very large recent messages (images, big
	// tool results) that push the last assistant turn far back, so progressively larger tail windows
	// are tried before giving up.
	public static string? LastAssistantLine(string path, int tailBytes = 65536)
	{
		using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
		long len = fs.Length;
		foreach (var window in new long[] { tailBytes, 1L << 20, 16L << 20 })
		{
			var line = ScanTail(fs, len, window);
			if (line != null) return line;
			if (window >= len) break; // already covered the whole file
		}
		return null;
	}

	// Reads the last `window` bytes and returns the last fully-contained line that parses as an
	// assistant turn. A truncated leading line simply fails to parse and is skipped.
	static string? ScanTail(FileStream fs, long len, long window)
	{
		long start = Math.Max(0, len - window);
		fs.Seek(start, SeekOrigin.Begin);
		using var reader = new StreamReader(fs, System.Text.Encoding.UTF8, detectEncodingFromByteOrderMarks: false, bufferSize: 4096, leaveOpen: true);
		var text = reader.ReadToEnd();

		var lines = text.Split('\n');
		for (int i = lines.Length - 1; i >= 0; i--)
		{
			var line = lines[i].Trim();
			if (line.Length == 0) continue;
			if (TranscriptParser.ParseAssistantLine(line) != null) return line;
		}
		return null;
	}
}
