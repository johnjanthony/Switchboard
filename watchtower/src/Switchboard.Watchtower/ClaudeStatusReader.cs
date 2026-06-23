using System.Net.Http;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// Fetches the Claude status page summary.json and parses it via ClaudeStatus.Parse (the branching
// logic lives in Core and is unit-tested there). Any failure collapses to ClaudeStatus.Unknown so
// the caller always has a value. Verified manually against the live endpoint (the Core test project
// cannot reference this UI assembly), mirroring SwitchboardStatsReader.
internal sealed class ClaudeStatusReader
{
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

	readonly string _summaryUrl;
	readonly Action<string, Exception>? _error;

	public ClaudeStatusReader(string summaryUrl, Action<string, Exception>? error = null)
	{
		_summaryUrl = summaryUrl;
		_error = error;
	}

	public async Task<ClaudeStatus> FetchAsync(CancellationToken ct)
	{
		var now = DateTime.UtcNow;
		try
		{
			using var resp = await Http.GetAsync(_summaryUrl, ct).ConfigureAwait(false);
			if (!resp.IsSuccessStatusCode) return ClaudeStatus.Unknown(now);
			var json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
			return ClaudeStatus.Parse(json, now) ?? ClaudeStatus.Unknown(now);
		}
		catch (OperationCanceledException) { throw; }
		catch (Exception ex) { _error?.Invoke("claude-status", ex); return ClaudeStatus.Unknown(now); }
	}
}
