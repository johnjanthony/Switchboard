using System.IO;
using System.Net.Http;
using System.Text;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// Queries the Antigravity language server's RetrieveUserQuotaSummary Connect RPC over loopback.
// The RPC port is discovered by probing the PID's listening ports (the --extension_server_port
// value refuses TLS). Self-signed loopback TLS is accepted for this client only.
internal sealed class AntigravityQuotaClient
{
	const string Body = """{"metadata":{"ideName":"antigravity","extensionName":"antigravity","locale":"en"}}""";
	const string QuotaPath = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary";
	const string ProbePath = "/exa.language_server_pb.LanguageServerService/GetUnleashData";

	static readonly HttpClient Http = new(new HttpClientHandler
	{
		ServerCertificateCustomValidationCallback = (_, _, _, _) => true,   // loopback self-signed cert
	})
	{ Timeout = TimeSpan.FromSeconds(3) };

	readonly Action<string>? _info;
	readonly Action<string, Exception>? _error;

	public AntigravityQuotaClient(Action<string>? info = null, Action<string, Exception>? error = null)
	{
		_info = info;
		_error = error;
	}

	public AntigravityQuotaSummary? Fetch(int pid, string csrfToken, IReadOnlyList<int> listeningPorts)
	{
		int? port = DiscoverPort(csrfToken, listeningPorts);
		if (port is null) { _info?.Invoke("no RPC port answered among the language server's listening ports"); return null; }
		try
		{
			var json = Post(port.Value, QuotaPath, csrfToken);
			return json is null ? null : AntigravityQuota.Parse(json);
		}
		catch (Exception ex) { _error?.Invoke("agy-quota-fetch", ex); return null; }
	}

	// The RPC port answers the cheap probe with a non-404; the command-line port does not.
	int? DiscoverPort(string csrfToken, IReadOnlyList<int> listeningPorts)
	{
		foreach (var p in listeningPorts)
		{
			try { if (Post(p, ProbePath, csrfToken) is not null) return p; }
			catch { /* try next */ }
		}
		return null;
	}

	string? Post(int port, string path, string csrfToken)
	{
		using var req = new HttpRequestMessage(HttpMethod.Post, $"https://127.0.0.1:{port}{path}")
		{
			Content = new StringContent(Body, Encoding.UTF8, "application/json"),
		};
		req.Headers.TryAddWithoutValidation("Accept", "application/json");
		req.Headers.TryAddWithoutValidation("Connect-Protocol-Version", "1");
		req.Headers.TryAddWithoutValidation("X-Codeium-Csrf-Token", csrfToken);
		using var resp = Http.Send(req);
		if (!resp.IsSuccessStatusCode) return null;
		using var reader = new StreamReader(resp.Content.ReadAsStream());
		return reader.ReadToEnd();
	}
}
