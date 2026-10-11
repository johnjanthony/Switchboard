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

	[Fact]
	public void Parse_reads_live_sessions_and_the_quota()
	{
		var json = """
			{"active_conversations":1,"pending_count":0,"oldest_pending_age_seconds":null,"away_mode":false,"healthy":true,
			 "sessions":{"total":1,"by_state":{"active":1},"items":[
			  {"session_id":"s1","name":"Fix undo","cwd":"C:/Work/X","surface":"windows","state":"active",
			   "model_label":"Opus 5.5 high","effort":"high","context_pct":0.412,"context_tokens":412000,"context_window":1000000}]},
			 "quota":{"five_hour":{"percent_used":23.5,"resets_at":"2026-10-10T18:00:00Z"},"seven_day":{"percent_used":61},
			          "received_at":"2026-10-10T17:02:11.5+00:00"}}
			""";
		var s = SwitchboardStats.Parse(json)!;
		Assert.Equal(new StatsSession("s1", "Fix undo", "C:/Work/X", "windows", "active", "Opus 5.5 high", "high", 412000, 1000000), Assert.Single(s.Sessions));
		Assert.Equal(new QuotaWindow(23.5, DateTimeOffset.Parse("2026-10-10T18:00:00Z")), s.Quota!.Value.Session);
		Assert.Equal(new QuotaWindow(61, null), s.Quota.Value.Weekly);
		Assert.Equal(DateTimeOffset.Parse("2026-10-10T17:02:11.5+00:00"), s.QuotaReceivedAt);
	}

	[Fact]
	public void Parse_of_an_older_server_without_items_or_quota_has_none()
	{
		var json = """{"active_conversations":0,"pending_count":0,"oldest_pending_age_seconds":null,"away_mode":false,"healthy":true,"sessions":{"total":0,"by_state":{}}}""";
		var s = SwitchboardStats.Parse(json)!;
		Assert.Empty(s.Sessions);
		Assert.Null(s.Quota);
		Assert.Null(s.QuotaReceivedAt);
	}

	[Fact]
	public void Parse_of_a_null_quota_has_none()
	{
		var json = """{"active_conversations":0,"pending_count":0,"oldest_pending_age_seconds":null,"away_mode":false,"healthy":true,"quota":null}""";
		Assert.Null(SwitchboardStats.Parse(json)!.Quota);
	}

	[Fact]
	public void Parse_of_a_zeroed_window_reads_zero_and_no_reset()
	{
		var json = """{"active_conversations":0,"pending_count":0,"oldest_pending_age_seconds":null,"away_mode":false,"healthy":true,"quota":{"five_hour":{"percent_used":0},"received_at":"2026-10-10T17:02:11+00:00"}}""";
		var q = SwitchboardStats.Parse(json)!.Quota!.Value;
		Assert.Equal(new QuotaWindow(0, null), q.Session);
		Assert.Equal(new QuotaWindow(0, null), q.Weekly);
	}

	[Fact]
	public void Parse_rejects_an_item_without_a_session_id()
	{
		var json = """{"active_conversations":0,"pending_count":0,"oldest_pending_age_seconds":null,"away_mode":false,"healthy":true,"sessions":{"items":[{"state":"idle"}]}}""";
		Assert.Null(SwitchboardStats.Parse(json));
	}
}
