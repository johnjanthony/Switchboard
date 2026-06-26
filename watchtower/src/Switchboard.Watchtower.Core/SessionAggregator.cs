namespace Switchboard.Watchtower.Core;

public static class SessionAggregator
{
	public static List<SessionModel> Collect(
		IEnumerable<string> windowsTranscripts,
		IEnumerable<(string distro, string path)> wslTranscripts,
		DateTime nowUtc,
		int liveThresholdSeconds,
		Action<string, Exception> onError)
	{
		var list = new List<SessionModel>();

		foreach (var path in windowsTranscripts)
			TryAdd(list, path, null, nowUtc, liveThresholdSeconds, onError);

		foreach (var (distro, path) in wslTranscripts)
			TryAdd(list, path, distro, nowUtc, liveThresholdSeconds, onError);

		list.Sort((a, b) => b.Pct.CompareTo(a.Pct)); // busiest first
		return list;
	}

	static void TryAdd(List<SessionModel> list, string path, string? distro, DateTime nowUtc, int liveThresholdSeconds, Action<string, Exception> onError)
	{
		try
		{
			list.Add(UsageReader.Read(path, distro, nowUtc, liveThresholdSeconds));
		}
		catch (Exception ex)
		{
			onError(path, ex);
			DateTime mtime;
			try { mtime = File.GetLastWriteTimeUtc(path); } catch { mtime = nowUtc; }
			var label = Path.GetFileName(Path.GetDirectoryName(path) ?? "") is { Length: > 0 } d ? d : "(error)";
			list.Add(new SessionModel(label, distro, 0, ModelWindowMap.DefaultWindow, null, SessionStatus.Idle, mtime, IsError: true, SessionId: Path.GetFileNameWithoutExtension(path)));
		}
	}
}
