using System.Text.Json;

namespace Switchboard.Watchtower.Core;

public static class AntigravityTranscriptParser
{
	public static SessionModel Parse(string[] lines, string sessionId, DateTime mtimeUtc, DateTime nowUtc, int liveThresholdSeconds)
	{
		long totalChars = 0;
		string? lastModel = null;
		string? foundCwd = null;
		string? title = null;

		foreach (var line in lines)
		{
			if (string.IsNullOrWhiteSpace(line)) continue;
			totalChars += line.Length;

			try
			{
				using var doc = JsonDocument.Parse(line);
				var root = doc.RootElement;
				if (root.ValueKind != JsonValueKind.Object) continue;

				// Check for Model Selection changes (keep the last one)
				if (root.TryGetProperty("content", out var contentElem) && contentElem.ValueKind == JsonValueKind.String)
				{
					var content = contentElem.GetString();
					if (!string.IsNullOrEmpty(content))
					{
						var modelMatch = ParseModelFromContent(content);
						if (modelMatch != null)
						{
							lastModel = modelMatch;
						}

						if (title == null)
						{
							var titleMatch = ParseUserRequestTitle(content);
							if (titleMatch != null)
							{
								title = titleMatch;
							}
						}
					}
				}

				// Check tool calls or step payload for Cwd if not found yet
				if (foundCwd == null)
				{
					foundCwd = ExtractCwdFromRoot(root);
				}
			}
			catch (JsonException)
			{
				// Ignore malformed JSON lines
			}
		}

		long contextTokens = Math.Max(0L, totalChars / 4L);
		string model = lastModel ?? "Gemini 3.1 Pro";
		long window = ModelWindowMap.EffectiveWindow(model, contextTokens);
		var status = ActiveClassifier.StatusFor(mtimeUtc, nowUtc, liveThresholdSeconds);
		string label = foundCwd != null ? CwdLabeler.Label(foundCwd) : "Antigravity";

		return new SessionModel(
			label,
			null,
			contextTokens,
			window,
			model,
			status,
			mtimeUtc,
			SessionId: sessionId,
			Name: title,
			NameSource: title != null ? "user" : null
		);
	}

	public static string? ParseModelFromContent(string content)
	{
		const string openTag = "<USER_SETTINGS_CHANGE>";
		const string closeTag = "</USER_SETTINGS_CHANGE>";

		int tagStart = content.LastIndexOf(openTag, StringComparison.OrdinalIgnoreCase);
		if (tagStart < 0) return null;
		tagStart += openTag.Length;

		int tagEnd = content.IndexOf(closeTag, tagStart, StringComparison.OrdinalIgnoreCase);
		if (tagEnd < 0) tagEnd = content.Length;

		string block = content.Substring(tagStart, tagEnd - tagStart);

		const string prefix = "setting `Model Selection` from ";
		int idx = block.IndexOf(prefix, StringComparison.OrdinalIgnoreCase);
		if (idx < 0) return null;

		int toIdx = block.IndexOf(" to ", idx + prefix.Length, StringComparison.OrdinalIgnoreCase);
		if (toIdx < 0) return null;

		int start = toIdx + 4;
		int end = block.Length;

		int dotEnd = block.IndexOf(". ", start, StringComparison.Ordinal);
		if (dotEnd >= 0 && dotEnd < end) end = dotEnd;

		int dotEnd2 = block.IndexOf(".\n", start, StringComparison.Ordinal);
		if (dotEnd2 >= 0 && dotEnd2 < end) end = dotEnd2;

		int dotEnd3 = block.IndexOf(".\r", start, StringComparison.Ordinal);
		if (dotEnd3 >= 0 && dotEnd3 < end) end = dotEnd3;

		string rawModel = block.Substring(start, end - start).Trim().TrimEnd('.');
		if (string.IsNullOrEmpty(rawModel) || !char.IsLetterOrDigit(rawModel[0])) return null;

		return rawModel;
	}

	public static string? ParseUserRequestTitle(string content)
	{
		const string startTag = "<USER_REQUEST>";
		const string endTag = "</USER_REQUEST>";

		int start = content.IndexOf(startTag, StringComparison.OrdinalIgnoreCase);
		if (start < 0) return null;
		start += startTag.Length;

		int end = content.IndexOf(endTag, start, StringComparison.OrdinalIgnoreCase);
		if (end < 0) end = content.Length;

		string prompt = content.Substring(start, end - start).Trim();
		if (string.IsNullOrEmpty(prompt)) return null;

		int newlineIdx = prompt.IndexOfAny(new[] { '\r', '\n' });
		if (newlineIdx >= 0) prompt = prompt.Substring(0, newlineIdx).Trim();

		return prompt.Length > 60 ? prompt.Substring(0, 57) + "..." : prompt;
	}

	public static bool IsValidPath(string? candidate)
	{
		if (string.IsNullOrWhiteSpace(candidate)) return false;
		if (candidate.Contains('<') || candidate.Contains('>') || candidate.Contains('\n') || candidate.Contains('\r')) return false;
		return candidate.Contains('/') || candidate.Contains('\\') || Path.IsPathRooted(candidate);
	}

	static string? ExtractCwdFromRoot(JsonElement root)
	{
		// 1. Check tool_calls array for arguments containing Cwd
		if (root.TryGetProperty("tool_calls", out var toolCalls) && toolCalls.ValueKind == JsonValueKind.Array)
		{
			foreach (var call in toolCalls.EnumerateArray())
			{
				if (call.TryGetProperty("args", out var args) && args.ValueKind == JsonValueKind.Object)
				{
					if (args.TryGetProperty("Cwd", out var cwdVal) && cwdVal.ValueKind == JsonValueKind.String)
					{
						var c = cwdVal.GetString();
						if (IsValidPath(c)) return c;
					}
					if (args.TryGetProperty("cwd", out var cwdVal2) && cwdVal2.ValueKind == JsonValueKind.String)
					{
						var c = cwdVal2.GetString();
						if (IsValidPath(c)) return c;
					}
				}
			}
		}

		// 2. Check content for switchboard identity inject: cwd='<path>'
		if (root.TryGetProperty("content", out var contentElem) && contentElem.ValueKind == JsonValueKind.String)
		{
			var text = contentElem.GetString();
			if (!string.IsNullOrEmpty(text))
			{
				const string marker = "cwd='";
				int idx = text.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
				if (idx >= 0)
				{
					int start = idx + marker.Length;
					int end = text.IndexOf('\'', start);
					if (end > start)
					{
						var cand = text.Substring(start, end - start);
						if (IsValidPath(cand)) return cand;
					}
				}

				// 3. Check for Active Document: <path> in step 0 ADDITIONAL_METADATA
				const string docMarker = "Active Document: ";
				int docIdx = text.IndexOf(docMarker, StringComparison.OrdinalIgnoreCase);
				if (docIdx >= 0)
				{
					int start = docIdx + docMarker.Length;
					int end = text.Length;
					int parenIdx = text.IndexOf(" (", start, StringComparison.Ordinal);
					if (parenIdx >= 0 && parenIdx < end) end = parenIdx;
					int newlineIdx = text.IndexOfAny(new[] { '\r', '\n' }, start);
					if (newlineIdx >= 0 && newlineIdx < end) end = newlineIdx;

					var docPath = text.Substring(start, end - start).Trim();
					if (!string.IsNullOrEmpty(docPath) && !docPath.Contains('<'))
					{
						try
						{
							var dir = Path.GetDirectoryName(docPath);
							if (IsValidPath(dir)) return dir;
						}
						catch { }
					}
				}
			}
		}

		return null;
	}
}
