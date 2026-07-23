using Switchboard.Watchtower.Core;
using Xunit;

public class CwdLabelerTests
{
	[Theory]
	[InlineData("C:\\Work\\rpdm\\next-gen", "rpdm/next-gen")]
	[InlineData("/home/janthony/work/rpdm", "work/rpdm")]
	[InlineData("C:\\Work", "C:/Work")]
	[InlineData("/", "(root)")]
	public void Label_returns_last_two_segments(string cwd, string expected)
	{
		Assert.Equal(expected, CwdLabeler.Label(cwd));
	}

	[Theory]
	[InlineData(null)]
	[InlineData("")]
	[InlineData("   ")]
	public void Label_handles_missing_cwd(string? cwd)
	{
		Assert.Equal("(unknown)", CwdLabeler.Label(cwd));
	}

	[Fact]
	public void Label_finds_git_repository_root_and_parent()
	{
		var tempDir = Path.Combine(Path.GetTempPath(), "CwdLabelerTest_" + Guid.NewGuid().ToString("N"));
		var parentDir = Path.Combine(tempDir, "ParentFolder");
		var repoDir = Path.Combine(parentDir, "RepoRoot");
		var gitDir = Path.Combine(repoDir, ".git");
		var subDir = Path.Combine(repoDir, "src", "sub");

		try
		{
			Directory.CreateDirectory(gitDir);
			Directory.CreateDirectory(subDir);

			// When CWD is deep inside subDir, label resolves to ParentFolder/RepoRoot
			var label = CwdLabeler.Label(subDir);
			Assert.Equal("ParentFolder/RepoRoot", label);

			// When CWD is at repo root itself, label resolves to ParentFolder/RepoRoot
			var rootLabel = CwdLabeler.Label(repoDir);
			Assert.Equal("ParentFolder/RepoRoot", rootLabel);
		}
		finally
		{
			if (Directory.Exists(tempDir))
			{
				Directory.Delete(tempDir, true);
			}
		}
	}
}

