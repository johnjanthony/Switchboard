namespace Switchboard.Watchtower.Core;

public static class ModelWindowMap
{
	public const long DefaultWindow = 200_000;
	public const long LargeWindow = 1_000_000;

	// Base window inferred from the model family. Claude Code transcripts record the BASE model
	// id (e.g. "claude-opus-4-7") even when 1M (Fast) mode is active — the "[1m]" marker is not
	// persisted — so Opus and Fable are treated as 1M here, matching how they are actually run.
	public static long WindowFor(string? model)
	{
		if (string.IsNullOrEmpty(model)) return DefaultWindow;
		if (model.Contains("[1m]", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
		if (model.Contains("opus", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
		if (model.Contains("fable", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
		if (model.Contains("gemini", StringComparison.OrdinalIgnoreCase)) return LargeWindow;
		return DefaultWindow;
	}

	// The window to measure against: at least the model's base window, and never smaller than the
	// observed context (a prompt cannot exceed its own window). Snapped up to a standard tier.
	public static long EffectiveWindow(string? model, long contextTokens)
	{
		long needed = Math.Max(WindowFor(model), contextTokens);
		if (needed <= DefaultWindow) return DefaultWindow;
		if (needed <= LargeWindow) return LargeWindow;
		return needed; // context somehow exceeds 1M — measure against itself rather than show >100%
	}
}
