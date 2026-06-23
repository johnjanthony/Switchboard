using System.Text.Json;

namespace Switchboard.Watchtower.Core;

public enum ClaudeStatusLevel { Operational, Minor, Major, Critical, Unknown }

/// <summary>
/// A parsed snapshot of the Claude status page (status.claude.com summary.json).
/// Level comes from status.indicator; IncidentNames lists the names of incidents
/// that are not yet resolved. FetchedAtUtc is stamped by the caller.
/// </summary>
public sealed record ClaudeStatus(
	ClaudeStatusLevel Level,
	string Description,
	IReadOnlyList<string> IncidentNames,
	DateTime FetchedAtUtc)
{
	// Incident statuses that mean "no longer active" and should be filtered out.
	static readonly HashSet<string> Closed = new(StringComparer.OrdinalIgnoreCase)
	{ "resolved", "postmortem", "completed" };

	public static ClaudeStatus Unknown(DateTime fetchedAtUtc)
		=> new(ClaudeStatusLevel.Unknown, "Status unavailable", Array.Empty<string>(), fetchedAtUtc);

	/// <summary>
	/// Parse summary.json. Returns null when the JSON is malformed or the
	/// status.indicator string is absent. An unrecognized (but present)
	/// indicator parses to Level.Unknown rather than null.
	/// </summary>
	public static ClaudeStatus? Parse(string json, DateTime fetchedAtUtc)
	{
		try
		{
			using var doc = JsonDocument.Parse(json);
			var root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object) return null;
			if (!root.TryGetProperty("status", out var status) || status.ValueKind != JsonValueKind.Object) return null;
			if (!status.TryGetProperty("indicator", out var ind) || ind.ValueKind != JsonValueKind.String) return null;

			var level = (ind.GetString() ?? "") switch
			{
				"none" => ClaudeStatusLevel.Operational,
				"minor" => ClaudeStatusLevel.Minor,
				"major" => ClaudeStatusLevel.Major,
				"critical" => ClaudeStatusLevel.Critical,
				_ => ClaudeStatusLevel.Unknown,
			};

			string description = status.TryGetProperty("description", out var desc) && desc.ValueKind == JsonValueKind.String
				? desc.GetString() ?? ""
				: "";

			var incidents = new List<string>();
			if (root.TryGetProperty("incidents", out var arr) && arr.ValueKind == JsonValueKind.Array)
			{
				foreach (var inc in arr.EnumerateArray())
				{
					if (inc.ValueKind != JsonValueKind.Object) continue;
					string st = inc.TryGetProperty("status", out var se) && se.ValueKind == JsonValueKind.String ? se.GetString() ?? "" : "";
					if (Closed.Contains(st)) continue;
					if (inc.TryGetProperty("name", out var ne) && ne.ValueKind == JsonValueKind.String && ne.GetString() is string n && n.Length > 0)
						incidents.Add(n);
				}
			}

			return new ClaudeStatus(level, description, incidents, fetchedAtUtc);
		}
		catch (JsonException) { return null; }
	}
}
