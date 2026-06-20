using System.Net.Http;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// Polls the Switchboard server's GET /stats endpoint, the same localhost trust model as /healthz.
// Thin glue over SwitchboardStats.Parse, which holds all the branching logic and is unit-tested in Core.
// This reader is verified manually (the Core test project cannot reference the UI assembly).
internal sealed class SwitchboardStatsReader
{
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

	readonly string _statsUrl;
	readonly Action<string, Exception>? _error;

	public SwitchboardStatsReader(string statsUrl, Action<string, Exception>? error = null)
	{
		_statsUrl = statsUrl;
		_error = error;
	}

	// Returns the parsed stats, or null when the server is unreachable, returns a non-success status,
	// or the body does not parse. Null is the caller's "Switchboard: unavailable" signal.
	public async Task<SwitchboardStats?> FetchAsync(CancellationToken ct)
	{
		try
		{
			using var resp = await Http.GetAsync(_statsUrl, ct).ConfigureAwait(false);
			if (!resp.IsSuccessStatusCode) return null;
			var json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
			return SwitchboardStats.Parse(json);
		}
		catch (OperationCanceledException) { throw; }
		catch (Exception ex) { _error?.Invoke("switchboard-stats", ex); return null; }
	}
}
