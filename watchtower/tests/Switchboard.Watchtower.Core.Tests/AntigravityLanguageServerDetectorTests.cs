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
}
