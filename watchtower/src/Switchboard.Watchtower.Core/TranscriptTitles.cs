using System.Text;

namespace Switchboard.Watchtower.Core;

/// Session display names from transcript title records. Transcripts are
/// append-only, so a cached byte offset makes re-scans read only new bytes.
/// The offset advances only past newline-terminated lines, so a partial
/// trailing line (Claude Code is still appending) is re-read next scan.
public static class TranscriptTitles
{
	sealed record Entry(long Offset, string? LastCustom, string? LastAi);
	static readonly Dictionary<string, Entry> Cache = new();
	static readonly Queue<string> Order = new();       // FIFO eviction order
	static readonly object Lock = new();

	internal const int MaxCacheEntries = 256;
	internal static int CacheEntryCount { get { lock (Lock) return Cache.Count; } }

	public static (string? Name, string? Source) Read(string path, string? sessionId)
	{
		var key = sessionId ?? path;
		Entry? cached;
		lock (Lock) { Cache.TryGetValue(key, out var e); cached = e; }
		long offset = cached?.Offset ?? 0;
		string? lastCustom = cached?.LastCustom;
		string? lastAi = cached?.LastAi;
		try
		{
			using var fs = new FileStream(path, FileMode.Open, FileAccess.Read,
				FileShare.ReadWrite | FileShare.Delete);
			if (fs.Length < offset) { offset = 0; lastCustom = null; lastAi = null; }  // file replaced/forked
			fs.Seek(offset, SeekOrigin.Begin);
			long available = fs.Length - offset;
			if (available > 0 && available <= int.MaxValue)
			{
				var buf = new byte[available];
				fs.ReadExactly(buf, 0, buf.Length);
				int lastNl = Array.LastIndexOf(buf, (byte)'\n');
				if (lastNl >= 0)
				{
					foreach (var raw in Encoding.UTF8.GetString(buf, 0, lastNl + 1).Split('\n'))
					{
						var line = raw.TrimEnd('\r');
						if (line.Length == 0) continue;
						var parsed = TranscriptParser.ParseTitleLine(line);
						if (parsed is null) continue;
						if (parsed.Value.Custom) lastCustom = parsed.Value.Title;
						else lastAi = parsed.Value.Title;
					}
					offset += lastNl + 1;                 // advance only past consumed, newline-terminated bytes
				}
				// no complete line yet -> consume nothing, leave offset
			}
		}
		catch (IOException) { }
		lock (Lock)
		{
			if (!Cache.ContainsKey(key)) Order.Enqueue(key);
			Cache[key] = new Entry(offset, lastCustom, lastAi);
			while (Cache.Count > MaxCacheEntries && Order.Count > 0)
			{
				var evict = Order.Dequeue();
				if (evict != key) Cache.Remove(evict);
			}
		}
		if (lastCustom is not null) return (lastCustom, "custom-title");
		if (lastAi is not null) return (lastAi, "ai-title");
		return (null, null);
	}
}
