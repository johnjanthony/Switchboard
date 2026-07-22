using Switchboard.Watchtower.Core;
using Xunit;

public class LastKnownTests
{
	static readonly DateTime Now = new(2026, 7, 15, 12, 0, 0, DateTimeKind.Utc);

	static string TempPath() =>
		Path.Combine(Path.GetTempPath(), "lastknown-" + Guid.NewGuid().ToString("N") + ".json");

	static SessionModel Session() => new(
		Label: "Switchboard", Distro: "Ubuntu-22.04", ContextTokens: 50_000, WindowSize: 200_000,
		Model: "claude-opus-4-8", Status: SessionStatus.Live, LastActiveUtc: Now.AddMinutes(-1),
		IsError: false, SessionId: "abc123", Name: "chunk6", NameSource: "hook");

	[Fact]
	public void Round_trips_full_state_through_disk()
	{
		var path = TempPath();
		try
		{
			var state = LastKnownStore.From(
				new[] { Session() }, Now.AddMinutes(-5),
				new QuotaUsage(new QuotaWindow(42.5, Now.AddHours(2)), new QuotaWindow(80.0, null)),
				new SwitchboardStats(3, 1, 247.0, true, true), Now);
			Assert.True(LastKnownStore.SaveTo(state, path));

			var loaded = LastKnownStore.LoadFrom(path);
			Assert.NotNull(loaded);
			Assert.Equal(Now, loaded!.SavedAtUtc);
			Assert.Equal(Now.AddMinutes(-5), loaded.LastActivityUtc);

			var sessions = LastKnownStore.ToSessionModels(loaded);
			Assert.Equal(new[] { Session() }, sessions);

			var quota = LastKnownStore.ToQuota(loaded);
			Assert.Equal(new QuotaUsage(new QuotaWindow(42.5, Now.AddHours(2)), new QuotaWindow(80.0, null)), quota);

			var stats = LastKnownStore.ToStats(loaded);
			Assert.Equal(new SwitchboardStats(3, 1, 247.0, true, true), stats);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Null_quota_and_stats_round_trip_as_null()
	{
		var path = TempPath();
		try
		{
			var state = LastKnownStore.From(Array.Empty<SessionModel>(), null, null, null, Now);
			Assert.True(LastKnownStore.SaveTo(state, path));
			var loaded = LastKnownStore.LoadFrom(path);
			Assert.NotNull(loaded);
			Assert.Empty(LastKnownStore.ToSessionModels(loaded!));
			Assert.Null(LastKnownStore.ToQuota(loaded!));
			Assert.Null(LastKnownStore.ToStats(loaded!));
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Missing_file_loads_as_null()
	{
		Assert.Null(LastKnownStore.LoadFrom(TempPath()));
	}

	[Fact]
	public void Corrupt_file_loads_as_null()
	{
		var path = TempPath();
		try
		{
			File.WriteAllText(path, "{ this is not valid json ");
			Assert.Null(LastKnownStore.LoadFrom(path));
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Save_is_atomic_and_cleans_its_tmp()
	{
		var path = TempPath();
		try
		{
			Assert.True(LastKnownStore.SaveTo(LastKnownStore.From(Array.Empty<SessionModel>(), null, null, null, Now), path));
			Assert.True(File.Exists(path));
			Assert.False(File.Exists(path + ".tmp"));
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void Sessions_fresh_within_sixty_minutes_only()
	{
		Assert.True(LastKnownStore.SessionsFresh(Now, Now.AddMinutes(59)));
		Assert.True(LastKnownStore.SessionsFresh(Now, Now.AddMinutes(60)));
		Assert.False(LastKnownStore.SessionsFresh(Now, Now.AddMinutes(61)));
	}

	[Fact]
	public void Unknown_status_string_degrades_to_idle()
	{
		var state = LastKnownStore.From(new[] { Session() }, null, null, null, Now);
		state.Sessions[0].Status = "SomethingFutur";
		var models = LastKnownStore.ToSessionModels(state);
		Assert.Equal(SessionStatus.Idle, models[0].Status);
	}

	[Fact]
	public void NeedsYou_round_trips_through_save_and_load()
	{
		var path = Path.Combine(Path.GetTempPath(), "lk-" + Guid.NewGuid().ToString("N") + ".json");
		var stats = new SwitchboardStats(1, 1, 90.0, false, true)
		{
			NeedsYou = new Dictionary<string, NeedsYouEntry> { ["sid-1"] = new("ask", 512.0) },
		};
		var state = LastKnownStore.From(Array.Empty<SessionModel>(), null, null, stats, Now);
		try
		{
			Assert.True(LastKnownStore.SaveTo(state, path));
			var loaded = LastKnownStore.LoadFrom(path);
			var roundTripped = LastKnownStore.ToStats(loaded!);
			Assert.NotNull(roundTripped);
			Assert.Equal(new NeedsYouEntry("ask", 512.0), roundTripped!.NeedsYou["sid-1"]);
		}
		finally { File.Delete(path); }
	}

	[Fact]
	public void ToStats_empty_needs_you_uses_shared_empty_instance()
	{
		// Preserves record value-equality for stats without needs-you entries.
		var state = LastKnownStore.From(Array.Empty<SessionModel>(), null, null, new SwitchboardStats(3, 1, 247.0, true, true), Now);
		Assert.Same(SwitchboardStats.EmptyNeedsYou, LastKnownStore.ToStats(state)!.NeedsYou);
	}

	[Fact]
	public void Pre_needs_you_cache_file_loads_with_empty_map()
	{
		var path = Path.Combine(Path.GetTempPath(), "lk-" + Guid.NewGuid().ToString("N") + ".json");
		File.WriteAllText(path, "{\"Version\":1,\"SavedAtUtc\":\"2026-06-12T12:00:00Z\",\"Sessions\":[]," +
			"\"Stats\":{\"ActiveConversations\":1,\"PendingCount\":0,\"OldestPendingAgeSeconds\":null,\"AwayMode\":false,\"Healthy\":true}}");
		try
		{
			var loaded = LastKnownStore.LoadFrom(path);
			Assert.NotNull(loaded);
			Assert.Empty(loaded!.Stats!.NeedsYou);
		}
		finally { File.Delete(path); }
	}

	static SessionModel CachedSession(string id) =>
		new("proj", null, 1000, 200000, "opus", SessionStatus.Idle, Now.AddHours(-3), false, id);

	[Fact]
	public void RenderableSessions_fresh_cache_returns_everything()
	{
		var state = LastKnownStore.From(new[] { CachedSession("sid-1"), CachedSession("sid-2") }, null, null, null, Now);
		var rendered = LastKnownStore.RenderableSessions(state, Now.AddMinutes(30));
		Assert.Equal(2, rendered.Count);
	}

	[Fact]
	public void RenderableSessions_stale_cache_returns_only_needs_you_rows()
	{
		var stats = new SwitchboardStats(1, 1, 90.0, false, true)
		{
			NeedsYou = new Dictionary<string, NeedsYouEntry> { ["sid-2"] = new("ask", 512.0) },
		};
		var state = LastKnownStore.From(new[] { CachedSession("sid-1"), CachedSession("sid-2") }, null, null, stats, Now);
		var rendered = LastKnownStore.RenderableSessions(state, Now.AddHours(2));
		Assert.Single(rendered);
		Assert.Equal("sid-2", rendered[0].SessionId);
	}

	[Fact]
	public void RenderableSessions_stale_cache_without_needs_you_returns_nothing()
	{
		var state = LastKnownStore.From(new[] { CachedSession("sid-1") }, null, null, null, Now);
		Assert.Empty(LastKnownStore.RenderableSessions(state, Now.AddHours(2)));
	}
}
