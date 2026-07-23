using System.Diagnostics;
using System.Management;                 // System.Management NuGet (Win32_Process command lines)
using System.Net.NetworkInformation;
using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

// Enumerates language-server processes + their listening ports, selects the best candidate,
// and fetches the quota summary. Returns null when no language server is running (IDE closed).
internal sealed class AntigravityQuotaPoller
{
	readonly AntigravityQuotaClient _client;
	readonly Action<string, Exception>? _error;

	public AntigravityQuotaPoller(Action<string>? info = null, Action<string, Exception>? error = null)
	{
		_error = error;
		_client = new AntigravityQuotaClient(info, error);
	}

	public AntigravityQuotaSummary? Poll()
	{
		try
		{
			var procs = LanguageServerProcesses();
			var candidates = AntigravityLanguageServerDetector.SelectOrdered(procs);
			foreach (var candidate in candidates)
			{
				var ports = ListeningPorts(candidate.Pid);
				var result = _client.Fetch(candidate.Pid, candidate.CsrfToken, ports);
				if (result is not null) return result;
			}
			return null;
		}
		catch (Exception ex) { _error?.Invoke("agy-quota-poll", ex); return null; }
	}

	static List<(int Pid, string CommandLine)> LanguageServerProcesses()
	{
		var result = new List<(int, string)>();
		using var searcher = new ManagementObjectSearcher(
			"SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'language_server_windows_x64.exe'");
		using var results = searcher.Get();
		foreach (ManagementObject mo in results)
		{
			using (mo)
			{
				var cmd = mo["CommandLine"] as string;
				if (cmd is null) continue;
				result.Add((Convert.ToInt32(mo["ProcessId"]), cmd));
			}
		}
		return result;
	}

	// Distinct local ports the PID is listening on. .NET has no owning-PID API for TCP listeners,
	// so shell out to netstat -ano and filter by PID (matches the antigravity-usage approach).
	static List<int> ListeningPorts(int pid)
	{
		var ports = new List<int>();
		var psi = new ProcessStartInfo("netstat", "-ano")
		{
			CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true,
		};
		using var p = Process.Start(psi);
		if (p is null) return ports;
		string outText = p.StandardOutput.ReadToEnd();
		p.WaitForExit(3000);
		foreach (var line in outText.Split('\n'))
		{
			if (!line.Contains("LISTENING")) continue;
			var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
			if (parts.Length < 5) continue;
			if (!int.TryParse(parts[^1], out var linePid) || linePid != pid) continue;
			var addr = parts[1];
			int colon = addr.LastIndexOf(':');
			if (colon >= 0 && int.TryParse(addr[(colon + 1)..], out var port) && !ports.Contains(port))
				ports.Add(port);
		}
		return ports;
	}
}
