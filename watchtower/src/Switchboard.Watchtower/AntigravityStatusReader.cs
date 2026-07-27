using System.Net.Http;
using System.Text;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// Thin client of the server's /widget-status?target=antigravity. GET returns the published view (parsed
// in Core by AntigravityServerStatus.ParseView); POST drives check/stop.
internal sealed class AntigravityStatusReader
{
	static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

	readonly string _baseUrl;
	readonly Action<string, Exception>? _error;

	public AntigravityStatusReader(string baseUrl, Action<string, Exception>? error = null)
	{
		_baseUrl = baseUrl;
		_error = error;
	}

	string StatusUrl => _baseUrl.Contains("?") ? _baseUrl + "&target=antigravity" : _baseUrl + "?target=antigravity";

	public async Task<AntigravityStatusView> GetViewAsync(CancellationToken ct)
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
			catch (Exception ex) { _error?.Invoke("antigravity-status-get", ex); return AntigravityServerStatus.ParseView(""); }
		}
		catch (Exception ex) { _error?.Invoke("antigravity-status-get", ex); return AntigravityServerStatus.ParseView(""); }
	}

	async Task<AntigravityStatusView> GetViewOnceAsync(CancellationToken ct)
	{
		using var resp = await Http.GetAsync(StatusUrl, ct).ConfigureAwait(false);
		if (!resp.IsSuccessStatusCode) return AntigravityServerStatus.ParseView("");
		var json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
		return AntigravityServerStatus.ParseView(json);
	}

	public async Task PostActionAsync(string action, CancellationToken ct)
	{
		try
		{
			var url = StatusUrl + "&action=" + action;
			using var content = new StringContent("{}", Encoding.UTF8, "application/json");
			using var resp = await Http.PostAsync(url, content, ct).ConfigureAwait(false);
			if (!resp.IsSuccessStatusCode)
				_error?.Invoke("antigravity-status-post", new HttpRequestException($"POST {url} returned {(int)resp.StatusCode}"));
		}
		catch (Exception ex) { _error?.Invoke("antigravity-status-post", ex); }
	}
}
