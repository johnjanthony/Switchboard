namespace Switchboard.Watchtower.Core;

public static class CwdLabeler
{
	public static string Label(string? cwd, int segments = 2)
	{
		if (string.IsNullOrWhiteSpace(cwd)) return "(unknown)";
		var parts = cwd.Replace('\\', '/').TrimEnd('/').Split('/', StringSplitOptions.RemoveEmptyEntries);
		if (parts.Length == 0) return "(root)";
		var take = Math.Min(segments, parts.Length);
		return string.Join('/', parts[^take..]);
	}
}
