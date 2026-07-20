using System.Net.Http;
using System.Text;
using System.Text.Json;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// POSTs the widget snapshot (rings + quota) to the Switchboard server's
// /widget-snapshot ingest, the same localhost trust model as SwitchboardStatsReader.
// Thin glue over WidgetSnapshotBuilder's payload (built + unit-tested in Core); this
// reader is verified manually (the Core test project cannot reference the UI assembly).
internal sealed class WidgetSnapshotPusher
{
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

	readonly string _url;
	readonly Action<string, Exception>? _error;

	public WidgetSnapshotPusher(string url, Action<string, Exception>? error = null)
	{
		_url = url;
		_error = error;
	}

	public async Task PushAsync(WidgetSnapshotPayload payload, CancellationToken ct)
	{
		try
		{
			var json = JsonSerializer.Serialize(payload);
			using var content = new StringContent(json, Encoding.UTF8, "application/json");
			using var resp = await Http.PostAsync(_url, content, ct).ConfigureAwait(false);
			// A non-success status is logged and dropped; the next push retries with fresh data.
			if (!resp.IsSuccessStatusCode)
				_error?.Invoke("widget-snapshot", new HttpRequestException($"POST {_url} returned {(int)resp.StatusCode}"));
		}
		catch (Exception ex) { _error?.Invoke("widget-snapshot", ex); }
	}
}
