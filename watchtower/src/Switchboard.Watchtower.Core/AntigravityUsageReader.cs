namespace Switchboard.Watchtower.Core;

public static class AntigravityUsageReader
{
	// Derives session_id (the conversation UUID) from directory hierarchy:
	// <brain>/<uuid>/.system_generated/logs/transcript_full.jsonl
	public static string DeriveSessionId(string path)
	{
		try
		{
			var dirLogs = Path.GetDirectoryName(path);
			var dirSysGen = dirLogs != null ? Path.GetDirectoryName(dirLogs) : null;
			var dirUuid = dirSysGen != null ? Path.GetDirectoryName(dirSysGen) : null;
			var uuid = dirUuid != null ? Path.GetFileName(dirUuid) : null;
			if (!string.IsNullOrEmpty(uuid) && uuid != "logs" && uuid != ".system_generated") return uuid;
		}
		catch { }
		var parentName = Path.GetFileName(Path.GetDirectoryName(path));
		return !string.IsNullOrEmpty(parentName) ? parentName : "unknown-antigravity-session";
	}

	public static SessionModel Read(string path, DateTime nowUtc, int liveThresholdSeconds)
	{
		string sessionId = DeriveSessionId(path);
		if (!File.Exists(path)) throw new InvalidDataException($"Antigravity transcript not found at {path}");

		string[] lines;
		using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
		using (var reader = new StreamReader(fs, System.Text.Encoding.UTF8))
		{
			var content = reader.ReadToEnd();
			if (string.IsNullOrWhiteSpace(content)) throw new InvalidDataException($"Empty Antigravity transcript at {path}");
			lines = content.Split('\n');
		}

		var mtime = File.GetLastWriteTimeUtc(path);
		return AntigravityTranscriptParser.Parse(lines, sessionId, mtime, nowUtc, liveThresholdSeconds);
	}
}
