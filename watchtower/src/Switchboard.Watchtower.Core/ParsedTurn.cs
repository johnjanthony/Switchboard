namespace Switchboard.Watchtower.Core;

public sealed record ParsedTurn(string? Model, Usage Usage, string? Cwd, string? Effort = null);
