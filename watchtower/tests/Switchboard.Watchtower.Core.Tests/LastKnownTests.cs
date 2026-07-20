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
}
