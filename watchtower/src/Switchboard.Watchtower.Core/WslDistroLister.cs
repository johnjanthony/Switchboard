using System.Diagnostics;
using System.Text;

namespace Switchboard.Watchtower.Core;

public sealed class WslDistroLister : IDistroLister
{
	public IReadOnlyList<string> RunningDistros()
	{
		try { return Parse(RunWsl("--list --running --quiet")); }
		catch { return Array.Empty<string>(); }
	}

	// wsl.exe emits UTF-16LE; names one per line. Strip, drop blanks.
	public static IReadOnlyList<string> Parse(string output)
	{
		return output
			.Replace("\0", "")
			.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
			.ToList();
	}

	const int WslTimeoutMs = 5000;

	static string RunWsl(string args)
	{
		var psi = new ProcessStartInfo("wsl.exe", args)
		{
			RedirectStandardOutput = true,
			UseShellExecute = false,
			CreateNoWindow = true,
			StandardOutputEncoding = Encoding.Unicode,
		};
		using var p = Process.Start(psi)!;
		var readTask = p.StandardOutput.ReadToEndAsync();
		if (!readTask.Wait(WslTimeoutMs))                 // was: unbounded ReadToEnd() then WaitForExit
		{
			try { p.Kill(entireProcessTree: true); } catch { /* already gone */ }
			return "";
		}
		p.WaitForExit(WslTimeoutMs);
		return readTask.Result;
	}
}
