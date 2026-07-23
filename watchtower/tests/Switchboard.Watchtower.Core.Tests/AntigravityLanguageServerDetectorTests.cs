using Switchboard.Watchtower.Core;
using Xunit;

namespace Switchboard.Watchtower.Core.Tests;

public class AntigravityLanguageServerDetectorTests
{
	const string LsCmd = @"c:\...\antigravity\bin\language_server_windows_x64.exe --enable_lsp --csrf_token ABC123 --extension_server_port 60427";

	[Fact]
	public void SelectBest_ExtractsCsrfFromLanguageServer()
	{
		var best = AntigravityLanguageServerDetector.SelectBest(new[] { (100, LsCmd) });
		Assert.NotNull(best);
		Assert.Equal(100, best!.Value.Pid);
		Assert.Equal("ABC123", best.Value.CsrfToken);
	}

	[Fact]
	public void SelectBest_IgnoresProcessesWithoutCsrf()
	{
		var best = AntigravityLanguageServerDetector.SelectBest(new[] { (1, @"c:\antigravity\Antigravity IDE.exe --type=gpu-process") });
		Assert.Null(best);
	}

	[Fact]
	public void SelectBest_PrefersHigherScoringCandidate()
	{
		// A bare csrf process (score 20+1) vs a full language_server (score 50+20+10+5+1).
		var weak = (2, @"foo antigravity --csrf_token WEAK");
		var strong = (3, LsCmd + " lsp exa.language_server_pb");
		var best = AntigravityLanguageServerDetector.SelectBest(new[] { weak, strong });
		Assert.Equal(3, best!.Value.Pid);
		Assert.Equal("ABC123", best.Value.CsrfToken);
	}

	[Fact]
	public void SelectBest_ReturnsNullOnEmptyInput()
	{
		Assert.Null(AntigravityLanguageServerDetector.SelectBest(System.Array.Empty<(int, string)>()));
	}

	[Fact]
	public void SelectBest_PrefersMainServerOverLspSubProcess()
	{
		var mainServer = (63376, @"c:\...\antigravity\bin\language_server_windows_x64.exe --csrf_token MAIN123 --extension_server_port 60389 --subclient_type ide");
		var lspWorker = (65916, @"c:\...\antigravity\bin\language_server_windows_x64.exe --enable_lsp --csrf_token LSP456 --extension_server_port 60427 --workspace_id file_c_3A_Work_Switchboard");

		var best = AntigravityLanguageServerDetector.SelectBest(new[] { lspWorker, mainServer });
		Assert.NotNull(best);
		Assert.Equal(63376, best!.Value.Pid);
		Assert.Equal("MAIN123", best.Value.CsrfToken);

		var ordered = AntigravityLanguageServerDetector.SelectOrdered(new[] { lspWorker, mainServer });
		Assert.Equal(2, ordered.Count);
		Assert.Equal(63376, ordered[0].Pid);
		Assert.Equal(65916, ordered[1].Pid);
	}
}
