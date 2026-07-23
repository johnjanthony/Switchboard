namespace Switchboard.Watchtower.Core;

public static class CwdLabeler
{
	public static string Label(string? cwd, int segments = 2, string? distro = null)
	{
		if (string.IsNullOrWhiteSpace(cwd)) return "(unknown)";
		var normalized = cwd.Replace('\\', '/').TrimEnd('/');
		var parts = normalized.Split('/', StringSplitOptions.RemoveEmptyEntries);
		if (parts.Length == 0) return "(root)";

		var gitLabel = TryGetGitRootLabel(cwd, distro);
		if (gitLabel != null) return gitLabel;

		var take = Math.Min(segments, parts.Length);
		return string.Join('/', parts[^take..]);
	}

	private static string? TryGetGitRootLabel(string cwd, string? distro)
	{
		try
		{
			string? probePath = GetProbePath(cwd, distro);
			if (string.IsNullOrEmpty(probePath)) return null;

			var current = new DirectoryInfo(probePath);
			while (current != null && current.Exists)
			{
				var gitDir = Path.Combine(current.FullName, ".git");
				if (Directory.Exists(gitDir) || File.Exists(gitDir))
				{
					var repoName = current.Name;
					var parent = current.Parent;
					if (parent != null && !string.IsNullOrEmpty(parent.Name))
					{
						var parentName = parent.Name.TrimEnd('\\', '/');
						return $"{parentName}/{repoName}";
					}
					return repoName;
				}
				current = current.Parent;
			}
		}
		catch
		{
			// Non-fatal: any filesystem/permission error falls back to standard label
		}
		return null;
	}

	private static string? GetProbePath(string cwd, string? distro)
	{
		if (cwd.StartsWith('/'))
		{
			if (!string.IsNullOrEmpty(distro))
			{
				var winPath = cwd.Replace('/', '\\');
				return $@"\\wsl.localhost\{distro}{winPath}";
			}
			return null;
		}
		return cwd;
	}
}

