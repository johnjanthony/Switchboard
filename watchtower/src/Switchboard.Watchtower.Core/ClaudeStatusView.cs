using System.Text.Json;

namespace Switchboard.Watchtower.Core;

// Parses the server's published widget/status view (GET /widget-status) into the
// ClaudeStatusView the display surfaces already render. The watch state machine
// lives on the server now (Phase 1b); Watchtower only renders + triggers.
public static class ClaudeServerStatus
{
	public static ClaudeStatusView ParseView(string json)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			var root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object) return Hidden();

			bool dotVisible = root.TryGetProperty("dot_visible", out var dv) && dv.ValueKind == JsonValueKind.True;
			bool hasData = root.TryGetProperty("has_data", out var hd) && hd.ValueKind == JsonValueKind.True;
			var level = ParseLevel(GetString(root, "level"));
			var button = ParseButton(GetString(root, "button"));
			string description = GetString(root, "description") ?? "";

			var incidents = new List<string>();
			if (root.TryGetProperty("incidents", out var arr) && arr.ValueKind == JsonValueKind.Array)
				foreach (var e in arr.EnumerateArray())
					if (e.ValueKind == JsonValueKind.String && e.GetString() is string s && s.Length > 0)
						incidents.Add(s);

			DateTime? fetchedAt = null;
			if (root.TryGetProperty("fetched_at", out var f) && f.ValueKind == JsonValueKind.String
				&& DateTime.TryParse(f.GetString(), out var dt))
				fetchedAt = dt.ToUniversalTime();

			return new ClaudeStatusView(dotVisible, level, hasData, description, incidents, fetchedAt, button);
		}
		catch (JsonException) { return Hidden(); }
	}

	static ClaudeStatusView Hidden()
		=> new(false, ClaudeStatusLevel.Unknown, false, "", Array.Empty<string>(), null, ClaudeStatusButton.CheckNow);

	static string? GetString(JsonElement root, string name)
		=> root.TryGetProperty(name, out var e) && e.ValueKind == JsonValueKind.String ? e.GetString() : null;

	static ClaudeStatusLevel ParseLevel(string? s) => s switch
	{
		"operational" => ClaudeStatusLevel.Operational,
		"minor" => ClaudeStatusLevel.Minor,
		"major" => ClaudeStatusLevel.Major,
		"critical" => ClaudeStatusLevel.Critical,
		_ => ClaudeStatusLevel.Unknown,
	};

	static ClaudeStatusButton ParseButton(string? s) => s switch
	{
		"stop" => ClaudeStatusButton.StopWatching,
		"clear" => ClaudeStatusButton.Clear,
		_ => ClaudeStatusButton.CheckNow,
	};
}
