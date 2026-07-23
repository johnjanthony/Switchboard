using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal enum QuotaStatus { Ok, NoCredentials, AuthRequired, RateLimited, Failed }

internal enum AnchorOutcome { Fired, SkippedWindowOpen, Failed }

internal readonly record struct QuotaResult(QuotaStatus Status, QuotaUsage? Usage);

// Fetches Claude plan usage (5h / 7d) the same way the CodeZeno monitor does: read the OAuth token
// from ~/.claude/.credentials.json and call GET /api/oauth/usage. Poll never spawns; an expired or
// rejected token keeps the last-known display. Starting a session window is the daily anchor's job
// (TryRunDailyAnchor -> RunHeadlessAnchorTurn). All calls block; run on a background thread.
internal sealed class QuotaService
{
	const string UsageUrl = "https://api.anthropic.com/api/oauth/usage";
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(30) };

	readonly Action<string>? _info;
	readonly Action<string, Exception>? _error;

	public QuotaService(Action<string>? info = null, Action<string, Exception>? error = null)
	{
		_info = info;
		_error = error;
	}

	static string CredentialsPath => Path.Combine(
		Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude", ".credentials.json");

	public QuotaResult Poll()
	{
		var creds = ReadCredentials();
		if (creds is null) return new QuotaResult(QuotaStatus.NoCredentials, null);

		// Option C: never spawn to refresh. An expired token reports AuthRequired so the app keeps
		// last-known usage (accurate while idle, since usage does not change). The daily anchor is the
		// only thing that ever starts a session window; a reactive refresh would start it at an unchosen time.
		if (QuotaParser.IsExpired(creds.Value.ExpiresAtMs, DateTimeOffset.UtcNow))
			return new QuotaResult(QuotaStatus.AuthRequired, null);

		return FetchUsage(creds.Value.Token);
	}

	(string Token, long? ExpiresAtMs)? ReadCredentials()
	{
		try
		{
			var path = CredentialsPath;
			if (!File.Exists(path)) return null;
			return QuotaParser.ParseCredentials(File.ReadAllText(path));
		}
		catch (Exception ex) { _error?.Invoke("quota-creds", ex); return null; }
	}

	QuotaResult FetchUsage(string token)
	{
		try
		{
			using var req = new HttpRequestMessage(HttpMethod.Get, UsageUrl);
			req.Headers.TryAddWithoutValidation("Authorization", $"Bearer {token}");
			req.Headers.TryAddWithoutValidation("anthropic-beta", "oauth-2025-04-20");
			using var resp = Http.Send(req);

			if (resp.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden)
				return new QuotaResult(QuotaStatus.AuthRequired, null);
			if ((int)resp.StatusCode == 429)
				return new QuotaResult(QuotaStatus.RateLimited, null);
			if (!resp.IsSuccessStatusCode)
			{
				_info?.Invoke($"usage endpoint returned HTTP {(int)resp.StatusCode}");
				return new QuotaResult(QuotaStatus.Failed, null);
			}

			using var reader = new StreamReader(resp.Content.ReadAsStream());
			var usage = QuotaParser.ParseUsage(reader.ReadToEnd());
			return usage is null
				? new QuotaResult(QuotaStatus.Failed, null)
				: new QuotaResult(QuotaStatus.Ok, usage);
		}
		catch (Exception ex) { _error?.Invoke("quota-fetch", ex); return new QuotaResult(QuotaStatus.Failed, null); }
	}

	// The once-a-day anchor decision. With a valid token, ask the server whether a 5-hour window is
	// already open; if so, skip (a window is already anchored, and firing would rotate the refresh
	// token under the live session that opened it). Otherwise (no open window, or an expired/rejected
	// token, which implies no recent activity) deliberately start the window. Blocking HTTP + up to a
	// 30s spawn: call from a background thread.
	public AnchorOutcome TryRunDailyAnchor(DateTimeOffset now)
	{
		var creds = ReadCredentials();
		if (creds is null) return AnchorOutcome.Failed;   // no auth at all; a spawn would only fail

		if (!QuotaParser.IsExpired(creds.Value.ExpiresAtMs, now))
		{
			var result = FetchUsage(creds.Value.Token);
			if (result.Status == QuotaStatus.Ok && result.Usage is QuotaUsage u
				&& u.Session.ResetsAt is DateTimeOffset reset && reset > now
				&& u.Session.Percentage > 0)
				return AnchorOutcome.SkippedWindowOpen;   // future reset WITH usage = a genuinely open window
			// Ok-with-no-open-window (incl. a stale future reset at 0 usage), or rate-limited / auth / failed: fall through and anchor.
		}

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
			// under global away mode the hook would drive this throwaway probe into an
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
