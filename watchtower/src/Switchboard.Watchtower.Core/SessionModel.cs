namespace Switchboard.Watchtower.Core;

public sealed record SessionModel(
	string Label,
	string? Distro,           // null for native Windows sessions
	long ContextTokens,
	long WindowSize,
	string? Model,
	SessionStatus Status,
	DateTime LastActiveUtc,
	bool IsError = false,
	string? SessionId = null,  // Claude Code session id (transcript filename stem); null when unknown
	string? Name = null,
	string? NameSource = null,
	string? Effort = null)     // observed effort of the last real turn; null for Antigravity and haiku
{
	public double Pct => WindowSize <= 0 ? 0 : (double)ContextTokens / WindowSize;
	public Severity Severity => SeverityClassifier.For(Pct);
}
