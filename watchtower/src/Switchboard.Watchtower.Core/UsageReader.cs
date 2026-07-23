namespace Switchboard.Watchtower.Core;

public static class UsageReader
{
	// Reads one transcript into a SessionModel. Throws InvalidDataException if no assistant turn is found,
	// or propagates IO exceptions (locked/unreadable file) for the caller to turn into an error model.
	public static SessionModel Read(string path, string? distro, DateTime nowUtc, int liveThresholdSeconds)
	{
		var line = TranscriptTail.LastAssistantLine(path);
		if (line is null) throw new InvalidDataException($"No assistant turn found in {path}");

		var turn = TranscriptParser.ParseAssistantLine(line)!;
		var mtime = File.GetLastWriteTimeUtc(path);
		var window = ModelWindowMap.EffectiveWindow(turn.Model, turn.Usage.ContextTokens);
		var status = ActiveClassifier.StatusFor(mtime, nowUtc, liveThresholdSeconds);
		var label = turn.Cwd is not null ? CwdLabeler.Label(turn.Cwd, distro: distro) : FolderFallbackLabel(path);

		var sessionId = Path.GetFileNameWithoutExtension(path);
		var (name, nameSource) = TranscriptTitles.Read(path, sessionId);
		return new SessionModel(label, distro, turn.Usage.ContextTokens, window, turn.Model, status, mtime,
			SessionId: sessionId, Name: name, NameSource: nameSource);
	}

	// When a transcript has no cwd field, fall back to the project folder name (the encoded cwd).
	static string FolderFallbackLabel(string path)
	{
		var dir = Path.GetFileName(Path.GetDirectoryName(path) ?? "");
		return string.IsNullOrEmpty(dir) ? "(unknown)" : dir;
	}
}
