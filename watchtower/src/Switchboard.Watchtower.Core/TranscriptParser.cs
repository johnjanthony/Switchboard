using System.Text.Json;

namespace Switchboard.Watchtower.Core;

public static class TranscriptParser
{
	// Claude Code writes these for turns it generated itself ("No response requested."): zero
	// usage, no effort. They are not real turns, so they must never replace the real model,
	// effort or context of the turn before them.
	const string SyntheticModel = "<synthetic>";

	// Returns a ParsedTurn for assistant lines carrying message.usage; null for any other line, a
	// synthetic line, or malformed JSON. Effort sits at the line root, beside `message`.
	public static ParsedTurn? ParseAssistantLine(string line)
	{
		if (string.IsNullOrWhiteSpace(line)) return null;
		try
		{
			using var doc = JsonDocument.Parse(line);
			var root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object) return null;

			string? cwd = root.TryGetProperty("cwd", out var c) && c.ValueKind == JsonValueKind.String ? c.GetString() : null;

			if (!root.TryGetProperty("message", out var msg) || msg.ValueKind != JsonValueKind.Object) return null;
			if (!msg.TryGetProperty("usage", out var usage) || usage.ValueKind != JsonValueKind.Object) return null;

			string? model = msg.TryGetProperty("model", out var m) && m.ValueKind == JsonValueKind.String ? m.GetString() : null;
			if (model == SyntheticModel) return null;
			string? effort = root.TryGetProperty("effort", out var e) && e.ValueKind == JsonValueKind.String ? e.GetString() : null;
			if (string.IsNullOrEmpty(effort)) effort = null;

			long Get(string name) => usage.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt64() : 0L;

			var u = new Usage(Get("input_tokens"), Get("cache_creation_input_tokens"), Get("cache_read_input_tokens"), Get("output_tokens"));
			return new ParsedTurn(model, u, cwd, effort);
		}
		catch (JsonException)
		{
			return null;
		}
	}

	public static (string Title, bool Custom)? ParseTitleLine(string line)
	{
		try
		{
			using var doc = JsonDocument.Parse(line);
			var root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object) return null;
			if (!root.TryGetProperty("type", out var t) || t.ValueKind != JsonValueKind.String) return null;
			var type = t.GetString();
			if (type == "custom-title" && root.TryGetProperty("customTitle", out var ct)
				&& ct.ValueKind == JsonValueKind.String && !string.IsNullOrEmpty(ct.GetString()))
				return (ct.GetString()!, true);
			if (type == "ai-title" && root.TryGetProperty("aiTitle", out var at)
				&& at.ValueKind == JsonValueKind.String && !string.IsNullOrEmpty(at.GetString()))
				return (at.GetString()!, false);
			return null;
		}
		catch (JsonException) { return null; }
	}
}
