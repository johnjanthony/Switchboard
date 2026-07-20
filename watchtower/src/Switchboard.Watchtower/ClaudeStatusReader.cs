using System.Net.Http;
using System.Text;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// Thin client of the server's /widget-status. GET returns the published view (parsed
// in Core by ClaudeServerStatus.ParseView); POST drives check/stop. The watch loop and
// the status.claude.com fetch live on the server now. Verified manually (the Core test
// project cannot reference this UI assembly), mirroring SwitchboardStatsReader.
internal sealed class ClaudeStatusReader
{
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

	readonly string _statusUrl;
	readonly Action<string, Exception>? _error;

	public ClaudeStatusReader(string statusUrl, Action<string, Exception>? error = null)
	{
		_statusUrl = statusUrl;
		_error = error;
	}

	// Returns the current server view, or a hidden idle view when the server is
	// unreachable / returns non-success / the body does not parse.
	//
	// One retry on a transport-level HttpRequestException: the server's keep-alive
	// timeout equals this client's poll interval, so a pooled connection is sometimes
	// closed by the server right as we reuse it, surfacing as a connection reset. The
	// retry opens a fresh connection and almost always succeeds; a real outage still
	// fails twice and collapses to the hidden idle view.
	public async Task<ClaudeStatusView> GetViewAsync(CancellationToken ct)
	{
		try
		{
			return await GetViewOnceAsync(ct).ConfigureAwait(false);
		}
		catch (HttpRequestException)
		{
			try
			{
				return await GetViewOnceAsync(ct).ConfigureAwait(false);
			}
			catch (Exception ex) { _error?.Invoke("claude-status-get", ex); return ClaudeServerStatus.ParseView(""); }
		}
		catch (Exception ex) { _error?.Invoke("claude-status-get", ex); return ClaudeServerStatus.ParseView(""); }
	}

	async Task<ClaudeStatusView> GetViewOnceAsync(CancellationToken ct)
	{
		using var resp = await Http.GetAsync(_statusUrl, ct).ConfigureAwait(false);
		if (!resp.IsSuccessStatusCode) return ClaudeServerStatus.ParseView("");
		var json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
		return ClaudeServerStatus.ParseView(json);
	}

	// Fire the check/stop action; the next GET poll reflects the result.
	public async Task PostActionAsync(string action, CancellationToken ct)
	{
		try
		{
			var url = _statusUrl + "?action=" + action;
			using var content = new StringContent("{}", Encoding.UTF8, "application/json");
			using var resp = await Http.PostAsync(url, content, ct).ConfigureAwait(false);
			if (!resp.IsSuccessStatusCode)
				_error?.Invoke("claude-status-post", new HttpRequestException($"POST {url} returned {(int)resp.StatusCode}"));
		}
		catch (Exception ex) { _error?.Invoke("claude-status-post", ex); }
	}
}
