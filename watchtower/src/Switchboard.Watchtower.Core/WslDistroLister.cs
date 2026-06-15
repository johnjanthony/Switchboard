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
		var output = p.StandardOutput.ReadToEnd();
		p.WaitForExit(5000);
		return output;
	}
}
