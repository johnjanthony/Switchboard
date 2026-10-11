using System.Diagnostics;
using System.IO;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal enum AnchorOutcome { Fired, SkippedWindowOpen, Failed }

// The once-a-day session anchor: deliberately starts the 5-hour window with one headless
// `claude -p .` turn, so the day's resets fall on John's schedule. It skips when the server's
// quota reading shows a window already open (firing would rotate the OAuth refresh token under
// the live session that opened it). Blocking, with up to a 30 s spawn: call from a background thread.
internal sealed class DailyAnchor
{
	readonly Action<string>? _info;
	readonly Action<string, Exception>? _error;

	public DailyAnchor(Action<string>? info = null, Action<string, Exception>? error = null)
	{
		_info = info;
		_error = error;
	}

	public AnchorOutcome TryRun(DateTimeOffset now, QuotaUsage? serverQuota)
	{
		if (DailyAnchorSchedule.WindowAlreadyOpen(serverQuota, now)) return AnchorOutcome.SkippedWindowOpen;
		return RunHeadlessAnchorTurn() ? AnchorOutcome.Fired : AnchorOutcome.Failed;
	}

	// Deliberately start (anchor) the 5-hour session window by running one headless `claude -p .` turn,
	// discarding output, up to 30s. Isolation is load-bearing: --setting-sources project excludes the
	// user settings layer so the switchboard away-mode Stop hook and MCP server do not drive this
	// throwaway turn into an ask_human() phone ping, and CLAUDECODE removal keeps it from acting nested.
	bool RunHeadlessAnchorTurn()
	{
		try
		{
			string claude = ResolveClaudePath();
			bool isCmd = claude.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase);
			_info?.Invoke($"anchoring session window via {claude}");

			var psi = new ProcessStartInfo
			{
				FileName = isCmd ? "cmd.exe" : claude,
				CreateNoWindow = true,
				UseShellExecute = false,
				RedirectStandardOutput = true,
				RedirectStandardError = true,
				RedirectStandardInput = true,
				// Don't inherit Watchtower's cwd (C:\Windows\system32 for a login-launched
				// widget), which is where the probe's session records were landing.
				WorkingDirectory = Directory.CreateDirectory(
					Path.Combine(Path.GetTempPath(), "switchboard-watchtower")).FullName,
			};
			if (isCmd) { psi.ArgumentList.Add("/c"); psi.ArgumentList.Add(claude); }
			psi.ArgumentList.Add("-p");
			psi.ArgumentList.Add(".");
			// Load only the project settings layer, not the user layer: the switchboard
			// plugin (its away-mode Stop hook) and MCP server live in user settings, and
			// under away mode the hook would drive this throwaway probe into an
			// ask_human() call that pings the phone. Excluding user keeps OAuth intact.
			psi.ArgumentList.Add("--setting-sources");
			psi.ArgumentList.Add("project");
			// Fire-and-forget probe: don't write a session transcript.
			psi.ArgumentList.Add("--no-session-persistence");
			// So the spawned CLI does not think it is running inside Claude Code.
			psi.Environment.Remove("CLAUDECODE");
			psi.Environment.Remove("CLAUDE_CODE_ENTRYPOINT");

			using var p = Process.Start(psi);
			if (p is null) return false;
			// Drain both streams concurrently; unread redirected output over the ~4KB pipe buffer
			// would otherwise block the child and stall this call for the full 30s timeout.
			var drainOut = p.StandardOutput.ReadToEndAsync();
			var drainErr = p.StandardError.ReadToEndAsync();
			p.StandardInput.Close();
			if (!p.WaitForExit(30000)) { try { p.Kill(true); } catch { /* already gone */ } return false; }
			return p.ExitCode == 0;
		}
		catch (Exception ex) { _error?.Invoke("quota-anchor", ex); return false; }
	}

	static string ResolveClaudePath()
	{
		foreach (var name in new[] { "claude.cmd", "claude" })
		{
			try
			{
				var psi = new ProcessStartInfo("where.exe", name)
				{
					CreateNoWindow = true,
					UseShellExecute = false,
					RedirectStandardOutput = true,
					RedirectStandardError = true,
				};
				using var p = Process.Start(psi);
				if (p is null) continue;
				var outTask = p.StandardOutput.ReadToEndAsync();
				var errTask = p.StandardError.ReadToEndAsync();   // drained so it can't block; unused
				if (!p.WaitForExit(5000)) { try { p.Kill(true); } catch { /* already gone */ } continue; }
				string output = outTask.Result;
				if (p.ExitCode == 0)
				{
					var first = output.Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.Length > 0);
					if (!string.IsNullOrEmpty(first)) return first;
				}
			}
			catch { /* try next */ }
		}
		return "claude.cmd";
	}
}
