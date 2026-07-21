using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal enum QuotaStatus { Ok, NoCredentials, AuthRequired, RateLimited, Failed }

internal readonly record struct QuotaResult(QuotaStatus Status, QuotaUsage? Usage);

// Fetches Claude plan usage (5h / 7d) the same way the CodeZeno monitor does: read the OAuth token
// from ~/.claude/.credentials.json, call GET /api/oauth/usage, and (if the token is expired or rejected)
// force a refresh by spawning the Claude CLI headlessly. All calls block; run on a background thread.
internal sealed class QuotaService
{
	const string UsageUrl = "https://api.anthropic.com/api/oauth/usage";
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(30) };

	readonly Action<string>? _info;
	readonly Action<string, Exception>? _error;
	readonly QuotaBackoff _backoff = new(TimeSpan.FromHours(24));

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

		// While backed off after an auth failure, spend nothing (no CLI spawn, no HTTP) until the
		// credentials file changes or the retry interval elapses. The skipped tick still reports
		// AuthRequired so the UI keeps showing the paused state.
		string fingerprint = QuotaBackoff.Fingerprint(creds.Value.Token, creds.Value.ExpiresAtMs);
		if (!_backoff.ShouldAttempt(fingerprint, DateTime.UtcNow))
			return new QuotaResult(QuotaStatus.AuthRequired, null);

		// Proactively refresh an expired token before spending a request on a guaranteed 401.
		if (QuotaParser.IsExpired(creds.Value.ExpiresAtMs, DateTimeOffset.UtcNow))
		{
			RefreshViaCli();
			creds = ReadCredentials();
			if (creds is null) return new QuotaResult(QuotaStatus.NoCredentials, null);
		}

		var result = FetchUsage(creds.Value.Token);
		if (result.Status == QuotaStatus.AuthRequired)
		{
			// Token was rejected despite not looking expired; refresh once and retry.
			RefreshViaCli();
			var refreshed = ReadCredentials();
			if (refreshed is null) return new QuotaResult(QuotaStatus.NoCredentials, null);
			creds = refreshed;
			result = FetchUsage(refreshed.Value.Token);
		}

		if (result.Status == QuotaStatus.AuthRequired)
		{
			// The refreshed token and the endpoint agree: auth is gone. Stop spending spawns.
			_backoff.RecordAuthFailure(QuotaBackoff.Fingerprint(creds.Value.Token, creds.Value.ExpiresAtMs), DateTime.UtcNow);
			_info?.Invoke("auth failure - quota polling backed off until credentials change (24h retry)");
		}
		else if (result.Status == QuotaStatus.Ok)
		{
			_backoff.RecordSuccess();
		}
		return result;
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

	// Force the Claude CLI to refresh its OAuth token (claude -p .), discarding output, up to 30s.
	void RefreshViaCli()
	{
		try
		{
			string claude = ResolveClaudePath();
			bool isCmd = claude.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase);
			_info?.Invoke($"refreshing Claude token via {claude}");

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
			if (p is null) return;
			// Drain both streams concurrently; unread redirected output over the ~4KB pipe buffer
			// would otherwise block the child and stall this call for the full 30s timeout.
			var drainOut = p.StandardOutput.ReadToEndAsync();
			var drainErr = p.StandardError.ReadToEndAsync();
			p.StandardInput.Close();
			if (!p.WaitForExit(30000)) { try { p.Kill(true); } catch { /* already gone */ } }
		}
		catch (Exception ex) { _error?.Invoke("quota-refresh", ex); }
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
