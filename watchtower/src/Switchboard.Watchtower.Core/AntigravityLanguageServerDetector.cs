using System.Text.RegularExpressions;

namespace Switchboard.Watchtower.Core;

public readonly record struct LanguageServerCandidate(int Pid, string CsrfToken);

/// <summary>
/// Pure selection of the best Antigravity language-server process and extraction of its CSRF token.
/// Mirrors antigravity-usage's scoring heuristic. The RPC port is NOT taken from the command line
/// (the --extension_server_port value refuses the TLS handshake); the caller discovers it separately.
/// </summary>
public static class AntigravityLanguageServerDetector
{
	static readonly Regex CsrfRe = new(@"--csrf_token\s+(\S+)", RegexOptions.Compiled);

	public static LanguageServerCandidate? SelectBest(IReadOnlyList<(int Pid, string CommandLine)> processes)
	{
		var ordered = SelectOrdered(processes);
		return ordered.Count > 0 ? ordered[0] : null;
	}

	public static IReadOnlyList<LanguageServerCandidate> SelectOrdered(IReadOnlyList<(int Pid, string CommandLine)> processes)
	{
		var list = new List<(LanguageServerCandidate Candidate, int Score)>();
		foreach (var (pid, cmd) in processes)
		{
			var m = CsrfRe.Match(cmd);
			if (!m.Success) continue;                 // no CSRF -> unusable
			int score = Score(cmd);
			list.Add((new LanguageServerCandidate(pid, m.Groups[1].Value), score));
		}
		return list.OrderByDescending(x => x.Score).Select(x => x.Candidate).ToList();
	}

	static int Score(string cmd)
	{
		int s = 0;
		if (cmd.Contains("antigravity", StringComparison.OrdinalIgnoreCase)) s += 1;
		if (cmd.Contains("--extension_server_port", StringComparison.OrdinalIgnoreCase)) s += 10;
		if (cmd.Contains("--csrf_token", StringComparison.OrdinalIgnoreCase)) s += 20;
		if (cmd.Contains("language_server", StringComparison.OrdinalIgnoreCase)
			|| cmd.Contains("exa.language_server_pb", StringComparison.OrdinalIgnoreCase)) s += 50;

		// Penalize sub-client / LSP worker processes so the primary extension server wins
		if (cmd.Contains("--enable_lsp", StringComparison.OrdinalIgnoreCase)
			|| cmd.Contains("--workspace_id", StringComparison.OrdinalIgnoreCase)) s -= 15;

		return s;
	}
}
