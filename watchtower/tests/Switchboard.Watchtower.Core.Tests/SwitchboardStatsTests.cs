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

	[Fact]
	public void Parse_absent_needs_you_yields_shared_empty_map()
	{
		// Old-server tolerance: absence is a version signal, not an error.
		var json = "{\"active_conversations\":3,\"pending_count\":2,\"oldest_pending_age_seconds\":247,\"away_mode\":true,\"healthy\":true}";
		var s = SwitchboardStats.Parse(json);
		Assert.NotNull(s);
		Assert.Same(SwitchboardStats.EmptyNeedsYou, s!.NeedsYou);
	}

	[Fact]
	public void Parse_reads_needs_you_entries()
	{
		var json = "{\"active_conversations\":1,\"pending_count\":1,\"oldest_pending_age_seconds\":10,\"away_mode\":false,\"healthy\":true," +
			"\"needs_you\":{\"sid-1\":{\"reason\":\"ask\",\"age_seconds\":512.5},\"sid-2\":{\"reason\":\"approval\",\"age_seconds\":33}}}";
		var s = SwitchboardStats.Parse(json);
		Assert.NotNull(s);
		Assert.Equal(2, s!.NeedsYou.Count);
		Assert.Equal(new NeedsYouEntry("ask", 512.5), s.NeedsYou["sid-1"]);
		Assert.Equal(new NeedsYouEntry("approval", 33.0), s.NeedsYou["sid-2"]);
	}

	[Fact]
	public void Parse_accepts_unknown_reason()
	{
		// Forward compatibility: a new server-side reason must still light the dot.
		var json = "{\"active_conversations\":0,\"pending_count\":0,\"oldest_pending_age_seconds\":null,\"away_mode\":false,\"healthy\":true," +
			"\"needs_you\":{\"sid-9\":{\"reason\":\"future-reason\",\"age_seconds\":1}}}";
		var s = SwitchboardStats.Parse(json);
		Assert.NotNull(s);
		Assert.Equal("future-reason", s!.NeedsYou["sid-9"].Reason);
	}

	[Fact]
	public void Parse_returns_null_when_needs_you_malformed()
	{
		// Present-but-malformed is a bug, not a version skew: strict like the rest of the parser.
		var notObject = "{\"active_conversations\":0,\"pending_count\":0,\"oldest_pending_age_seconds\":null,\"away_mode\":false,\"healthy\":true," +
			"\"needs_you\":[]}";
		Assert.Null(SwitchboardStats.Parse(notObject));
		var missingReason = "{\"active_conversations\":0,\"pending_count\":0,\"oldest_pending_age_seconds\":null,\"away_mode\":false,\"healthy\":true," +
			"\"needs_you\":{\"sid-1\":{\"age_seconds\":5}}}";
		Assert.Null(SwitchboardStats.Parse(missingReason));
	}
}
