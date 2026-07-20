namespace Switchboard.Watchtower.Core;

/// Windows Terminal tab-title heartbeat: Claude Code sets an OSC title with a
/// Braille spinner while working and a "U+2733 " prefix when idle or parked at a
/// permission prompt. Parsing is pure Core so it is unit-testable; the UIA
/// enumeration that produces the titles lives in the app project.
public static class TabTitles
{
	public static (string? State, string? Name) Classify(string? title)
	{
		var t = title?.Trim();
		if (string.IsNullOrEmpty(t)) return (null, null);
		var first = char.ConvertToUtf32(t, 0);
		if (first >= 0x2800 && first <= 0x28FF)
			return ("working", t[char.ConvertFromUtf32(first).Length..].Trim());
		if (first == 0x2733)
		{
			var rest = t[char.ConvertFromUtf32(first).Length..];
			if (rest.Length > 0 && rest[0] == '\uFE0F') rest = rest[1..];  // optional emoji variation selector
			return ("star", rest.Trim());
		}
		return (null, t);
	}

	public static Dictionary<string, string> Correlate(
		IReadOnlyList<(string? State, string? Name)> tabs, IReadOnlyList<SessionModel> sessions)
	{
		var result = new Dictionary<string, string>();
		var tabNames = tabs.Where(x => x.State is not null && !string.IsNullOrEmpty(x.Name))
			.GroupBy(x => x.Name!).Where(g => g.Count() == 1).ToDictionary(g => g.Key, g => g.First().State!);
		var byName = sessions.Where(s => s.SessionId is not null && !string.IsNullOrEmpty(s.Name))
			.GroupBy(s => s.Name!).Where(g => g.Count() == 1).ToDictionary(g => g.Key, g => g.First().SessionId!);
		foreach (var (name, state) in tabNames)
			if (byName.TryGetValue(name, out var sid))
				result[sid] = state;
		return result;
	}
}
