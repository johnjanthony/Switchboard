using Switchboard.Watchtower.Core;
using Xunit;

public class SwitchboardStatsTests
{
	[Fact]
	public void Parse_reads_all_fields()
	{
		var json = "{\"active_conversations\":3,\"pending_count\":2,\"oldest_pending_age_seconds\":247,\"away_mode\":true,\"healthy\":true}";
		var s = SwitchboardStats.Parse(json);
		Assert.NotNull(s);
		Assert.Equal(3, s!.ActiveConversations);
		Assert.Equal(2, s.PendingCount);
		Assert.Equal(247.0, s.OldestPendingAgeSeconds);
		Assert.True(s.AwayMode);
		Assert.True(s.Healthy);
	}

	[Fact]
	public void Parse_allows_null_oldest_age()
	{
		var json = "{\"active_conversations\":0,\"pending_count\":0,\"oldest_pending_age_seconds\":null,\"away_mode\":false,\"healthy\":true}";
		var s = SwitchboardStats.Parse(json);
		Assert.NotNull(s);
		Assert.Equal(0, s!.ActiveConversations);
		Assert.Equal(0, s.PendingCount);
		Assert.Null(s.OldestPendingAgeSeconds);
		Assert.False(s.AwayMode);
		Assert.True(s.Healthy);
	}

	[Fact]
	public void Parse_returns_null_when_required_field_missing()
	{
		// pending_count omitted.
		var json = "{\"active_conversations\":3,\"oldest_pending_age_seconds\":247,\"away_mode\":true,\"healthy\":true}";
		Assert.Null(SwitchboardStats.Parse(json));
	}

	[Fact]
	public void Parse_returns_null_on_malformed_json()
	{
		Assert.Null(SwitchboardStats.Parse("not json"));
	}
}
