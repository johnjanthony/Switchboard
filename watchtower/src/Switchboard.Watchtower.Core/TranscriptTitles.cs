namespace Switchboard.Watchtower.Core;

/// Session display names from transcript title records. Transcripts are
/// append-only, so a cached byte offset makes re-scans read only new bytes;
/// the tail-based assistant scan cannot see titles that sit high in the file.
public static class TranscriptTitles
{
	sealed record Entry(long Offset, string? LastCustom, string? LastAi);
	static readonly Dictionary<string, Entry> Cache = new();
	static readonly object Lock = new();

	public static (string? Name, string? Source) Read(string path, string? sessionId)
	{
		var key = sessionId ?? path;
		Entry cached;
		lock (Lock) { Cache.TryGetValue(key, out cached!); }
		long offset = cached?.Offset ?? 0;
		string? lastCustom = cached?.LastCustom;
		string? lastAi = cached?.LastAi;
		try
		{
			using var fs = new FileStream(path, FileMode.Open, FileAccess.Read,
				FileShare.ReadWrite | FileShare.Delete);
			if (fs.Length < offset)
			{
				offset = 0; lastCustom = null; lastAi = null;  // file replaced/forked
			}
			fs.Seek(offset, SeekOrigin.Begin);
			using var reader = new StreamReader(fs);
			string? line;
			while ((line = reader.ReadLine()) != null)
			{
				var parsed = TranscriptParser.ParseTitleLine(line);
				if (parsed is null) continue;
				if (parsed.Value.Custom) lastCustom = parsed.Value.Title;
				else lastAi = parsed.Value.Title;
			}
			offset = fs.Length;
		}
		catch (IOException) { }
		lock (Lock) { Cache[key] = new Entry(offset, lastCustom, lastAi); }
		if (lastCustom is not null) return (lastCustom, "custom-title");
		if (lastAi is not null) return (lastAi, "ai-title");
		return (null, null);
	}
}
