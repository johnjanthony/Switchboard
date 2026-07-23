namespace Switchboard.Watchtower.Core;

public static class AntigravitySessionScanner
{
	public static IEnumerable<string> DefaultRoots
	{
		get
		{
			var userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
			yield return Path.Combine(userProfile, ".gemini", "antigravity-cli", "brain");
			yield return Path.Combine(userProfile, ".gemini", "antigravity-ide", "brain");
		}
	}

	public static IEnumerable<string> ActiveTranscripts(
		IEnumerable<string> roots,
		DateTime nowUtc,
		int activeWindowMinutes,
		IReadOnlySet<string>? retainIds = null)
	{
		var candidates = new Dictionary<string, (string Path, DateTime Mtime)>(StringComparer.OrdinalIgnoreCase);

		foreach (var root in roots)
		{
			if (!Directory.Exists(root)) continue;

			var brainDirs = SafeScan.Materialize(() => Directory.EnumerateDirectories(root));
			foreach (var brainDir in brainDirs)
			{
				var transcriptPath = Path.Combine(brainDir, ".system_generated", "logs", "transcript_full.jsonl");
				if (!File.Exists(transcriptPath)) continue;

				DateTime mtime;
				try { mtime = File.GetLastWriteTimeUtc(transcriptPath); }
				catch { continue; }

				var sessionId = Path.GetFileName(brainDir);

				if (ActiveClassifier.IsActive(mtime, nowUtc, activeWindowMinutes) || ActiveClassifier.IsRetainedById(sessionId, retainIds))
				{
					if (!candidates.TryGetValue(sessionId, out var existing) || mtime > existing.Mtime)
					{
						candidates[sessionId] = (transcriptPath, mtime);
					}
				}
			}
		}

		foreach (var entry in candidates.Values)
		{
			yield return entry.Path;
		}
	}
}
