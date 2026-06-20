using Switchboard.Watchtower.Core;
using Xunit;

public class RelativeTimeTests
{
	static readonly DateTime Now = new(2026, 6, 12, 12, 0, 0, DateTimeKind.Utc);

	[Theory]
	[InlineData(30, "just now")]
	[InlineData(59, "just now")]
	[InlineData(60, "1 minute ago")]
	[InlineData(1500, "25 minutes ago")]   // 25 minutes
	[InlineData(3600, "1 hour ago")]
	[InlineData(7200, "2 hours ago")]
	[InlineData(90000, "1 day ago")]       // 25 hours
	[InlineData(180000, "2 days ago")]     // 50 hours
	public void Ago_formats_coarsely(long secsAgo, string expected)
	{
		Assert.Equal(expected, RelativeTime.Ago(Now.AddSeconds(-secsAgo), Now));
	}

	[Fact]
	public void Future_clamps_to_just_now()
	{
		Assert.Equal("just now", RelativeTime.Ago(Now.AddSeconds(30), Now));
	}
}
