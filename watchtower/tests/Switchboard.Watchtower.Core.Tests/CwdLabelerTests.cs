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
}
