namespace Switchboard.Watchtower.Core;

/// Shared safe-append logger. Logging must never crash the widget, so every
/// failure is swallowed. `Program`'s global exception handlers and `AppHost`
/// both write through this so the log path lives in one place.
public static class WatchtowerLog
{
	static readonly object Lock = new();

	public static string DefaultLogPath => Path.Combine(
		Path.GetDirectoryName(AppConfig.DefaultPath)!, "log.txt");

	public static void Info(string source, string message, string? path = null)
		=> Append(source, message, path);

	public static void Error(string source, Exception ex, string? path = null)
		=> Append(source, $"{ex.GetType().Name}: {ex.Message}", path);

	static void Append(string source, string message, string? path)
	{
		try
		{
			path ??= DefaultLogPath;
			Directory.CreateDirectory(Path.GetDirectoryName(path)!);
			lock (Lock) File.AppendAllText(path, $"{DateTime.Now:s} [{source}] {message}{Environment.NewLine}");
		}
		catch { /* logging must never crash the widget */ }
	}
}
