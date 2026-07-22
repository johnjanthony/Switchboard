namespace Switchboard.Watchtower.Core;

public static class WindowsSessionScanner
{
	public static string DefaultProjectsRoot =>
		Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude", "projects");

	// Top-level session transcripts are <projectsRoot>/<encoded-cwd>/<uuid>.jsonl. Subagent transcripts
	// live one level deeper under <uuid>/subagents/ and are excluded by TopDirectoryOnly enumeration.
	public static IEnumerable<string> ActiveTranscripts(string projectsRoot, DateTime nowUtc, int activeWindowMinutes, IReadOnlySet<string>? retainIds = null)
	{
		if (!Directory.Exists(projectsRoot)) yield break;

		foreach (var projDir in Directory.EnumerateDirectories(projectsRoot))
		{
			var files = SafeScan.Materialize(() => Directory.EnumerateFiles(projDir, "*.jsonl", SearchOption.TopDirectoryOnly));
			foreach (var (file, mtime) in SafeScan.WithMtimes(files, File.GetLastWriteTimeUtc))
				if (ActiveClassifier.IsActive(mtime, nowUtc, activeWindowMinutes) || ActiveClassifier.IsRetained(file, retainIds)) yield return file;
		}
	}

	// The newest top-level transcript mtime across all projects, ignoring the active window. Used to
	// report "last active agent N ago" once every session has aged out. Null when no transcripts exist.
	public static DateTime? MostRecentActivityUtc(string projectsRoot)
	{
		if (!Directory.Exists(projectsRoot)) return null;

		DateTime? max = null;
		foreach (var projDir in Directory.EnumerateDirectories(projectsRoot))
		{
			var files = SafeScan.Materialize(() => Directory.EnumerateFiles(projDir, "*.jsonl", SearchOption.TopDirectoryOnly));
			foreach (var (_, mtime) in SafeScan.WithMtimes(files, File.GetLastWriteTimeUtc))
				if (max is null || mtime > max) max = mtime;
		}
		return max;
	}
}
