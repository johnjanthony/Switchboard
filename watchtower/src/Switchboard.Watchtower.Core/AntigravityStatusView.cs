using System.Text.Json;

namespace Switchboard.Watchtower.Core;

public enum AntigravityStatusLevel { Operational, Minor, Major, Critical, Unknown }

public enum AntigravityStatusButton { CheckNow, StopWatching, Clear }

// The view the display surfaces render (dot + popup + tray item). Pure data.
public sealed record AntigravityStatusView(
	bool DotVisible,
	AntigravityStatusLevel DotLevel,
	bool HasData,
	string Description,
	IReadOnlyList<string> IncidentNames,
	DateTime? FetchedAtUtc,
	AntigravityStatusButton Button,
	bool LocalLspHealthy = true);

// Parses the server's published widget/status view (GET /widget-status?target=antigravity) into the
// AntigravityStatusView the display surfaces render.
public static class AntigravityServerStatus
{
	public static AntigravityStatusView ParseView(string json)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			var root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object) return Hidden();

			bool dotVisible = root.TryGetProperty("dot_visible", out var dv) && dv.ValueKind == JsonValueKind.True;
			bool hasData = root.TryGetProperty("has_data", out var hd) && hd.ValueKind == JsonValueKind.True;
			bool localLspHealthy = !root.TryGetProperty("local_lsp_healthy", out var lsp) || lsp.ValueKind != JsonValueKind.False;
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

			return new AntigravityStatusView(dotVisible, level, hasData, description, incidents, fetchedAt, button, localLspHealthy);
		}
		catch (JsonException) { return Hidden(); }
	}

	static AntigravityStatusView Hidden()
		=> new(false, AntigravityStatusLevel.Unknown, false, "", Array.Empty<string>(), null, AntigravityStatusButton.CheckNow, true);

	static string? GetString(JsonElement root, string name)
		=> root.TryGetProperty(name, out var e) && e.ValueKind == JsonValueKind.String ? e.GetString() : null;

	static AntigravityStatusLevel ParseLevel(string? s) => s switch
	{
		"operational" => AntigravityStatusLevel.Operational,
		"minor" => AntigravityStatusLevel.Minor,
		"major" => AntigravityStatusLevel.Major,
		"critical" => AntigravityStatusLevel.Critical,
		_ => AntigravityStatusLevel.Unknown,
	};

	static AntigravityStatusButton ParseButton(string? s) => s switch
	{
		"stop" => AntigravityStatusButton.StopWatching,
		"clear" => AntigravityStatusButton.Clear,
		_ => AntigravityStatusButton.CheckNow,
	};
}
