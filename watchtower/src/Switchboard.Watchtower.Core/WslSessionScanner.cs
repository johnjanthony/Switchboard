namespace Switchboard.Watchtower.Core;

public static class WslSessionScanner
{
	static readonly string[] SystemDistros = { "docker-desktop", "docker-desktop-data" };

	// glob: distro name -> candidate top-level transcript paths under that distro.
	// mtimeOf: file path -> last write time (UTC); defaults to File.GetLastWriteTimeUtc.
	public static IEnumerable<(string distro, string path)> ActiveTranscripts(
		IDistroLister lister,
		DateTime nowUtc,
		int activeWindowMinutes,
		Func<string, IEnumerable<string>> glob,
		Func<string, DateTime>? mtimeOf = null)
	{
		mtimeOf ??= File.GetLastWriteTimeUtc;

		foreach (var distro in lister.RunningDistros())
		{
			if (SystemDistros.Contains(distro, StringComparer.OrdinalIgnoreCase)) continue;

			foreach (var path in glob(distro))
			{
				DateTime mtime;
				try { mtime = mtimeOf(path); }
				catch (IOException) { continue; }
				if (ActiveClassifier.IsActive(mtime, nowUtc, activeWindowMinutes)) yield return (distro, path);
			}
		}
	}

	// The newest transcript mtime across all (non-system) running distros, ignoring the active window.
	// Mirrors ActiveTranscripts' enumeration but returns just the max mtime. Null when nothing is globbed.
	public static DateTime? MostRecentActivityUtc(
		IDistroLister lister,
		Func<string, IEnumerable<string>> glob,
		Func<string, DateTime>? mtimeOf = null)
	{
		mtimeOf ??= File.GetLastWriteTimeUtc;

		DateTime? max = null;
		foreach (var distro in lister.RunningDistros())
		{
			if (SystemDistros.Contains(distro, StringComparer.OrdinalIgnoreCase)) continue;

			foreach (var path in glob(distro))
			{
				DateTime mtime;
				try { mtime = mtimeOf(path); }
				catch (IOException) { continue; }
				if (max is null || mtime > max) max = mtime;
			}
		}
		return max;
	}

	// Real glob over the WSL filesystem bridge: \\wsl.localhost\<distro>\(home\*|root)\.claude\projects\<proj>\<uuid>.jsonl
	public static IEnumerable<string> DefaultGlob(string distro)
	{
		var bridgeRoot = $@"\\wsl.localhost\{distro}";
		var homeBases = new List<string>();

		var homeDir = Path.Combine(bridgeRoot, "home");
		if (SafeDirExists(homeDir))
		{
			foreach (var user in SafeEnumerateDirs(homeDir)) homeBases.Add(user);
		}
		homeBases.Add(Path.Combine(bridgeRoot, "root"));

		foreach (var home in homeBases)
		{
			var projects = Path.Combine(home, ".claude", "projects");
			if (!SafeDirExists(projects)) continue;
			foreach (var projDir in SafeEnumerateDirs(projects))
			{
				IEnumerable<string> files;
				try { files = Directory.EnumerateFiles(projDir, "*.jsonl", SearchOption.TopDirectoryOnly); }
				catch { continue; }
				foreach (var f in files) yield return f;
			}
		}
	}

	static bool SafeDirExists(string p) { try { return Directory.Exists(p); } catch { return false; } }
	static IEnumerable<string> SafeEnumerateDirs(string p)
	{
		try { return Directory.EnumerateDirectories(p); } catch { return Array.Empty<string>(); }
	}
}
