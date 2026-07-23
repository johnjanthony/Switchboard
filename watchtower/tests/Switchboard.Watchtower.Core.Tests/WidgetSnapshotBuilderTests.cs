using System.Text.Json;
using Switchboard.Watchtower.Core;
using Xunit;

public class WidgetSnapshotBuilderTests
{
	static readonly DateTimeOffset Pushed = new(2026, 6, 25, 16, 0, 0, TimeSpan.Zero);

	static SessionModel Session(string? sid, double pct, SessionStatus status = SessionStatus.Live)
	{
		// window 200000, contextTokens = pct * window so SessionModel.Pct == pct
		long window = 200_000;
		return new SessionModel("label", null, (long)(pct * window), window, "claude-opus-4-8", status, Pushed.UtcDateTime, IsError: false, SessionId: sid);
	}

	[Fact]
	public void Build_keys_rings_and_maps_fields()
	{
		var p = WidgetSnapshotBuilder.Build(new[] { Session("abc", 0.8) }, null, Pushed);
		Assert.Single(p.Rings);
		var r = p.Rings[0];
		Assert.Equal("abc", r.SessionId);
		Assert.InRange(r.Pct, 0.79, 0.81);
		Assert.Equal("live", r.Status);
		Assert.Equal("claude-opus-4-8", r.Model);
		Assert.Equal(200_000, r.Window);
		Assert.False(r.IsError);
		Assert.Null(p.Quota);
		Assert.Equal(Pushed.ToString("o"), p.PushedAt);
	}

	[Fact]
	public void Build_drops_rings_without_session_id()
	{
		var p = WidgetSnapshotBuilder.Build(new[] { Session(null, 0.5), Session("keep", 0.5) }, null, Pushed);
		Assert.Single(p.Rings);
		Assert.Equal("keep", p.Rings[0].SessionId);
	}

	[Fact]
	public void Build_maps_idle_status()
	{
		var p = WidgetSnapshotBuilder.Build(new[] { Session("x", 0.1, SessionStatus.Idle) }, null, Pushed);
		Assert.Equal("idle", p.Rings[0].Status);
	}

	[Fact]
	public void Build_maps_quota_percentage_to_fraction()
	{
		var quota = new QuotaUsage(
			new QuotaWindow(42, new DateTimeOffset(2026, 6, 25, 20, 0, 0, TimeSpan.Zero)),
			new QuotaWindow(18, null));
		var p = WidgetSnapshotBuilder.Build(Array.Empty<SessionModel>(), quota, Pushed);
		Assert.NotNull(p.Quota);
		Assert.InRange(p.Quota!.Session.Pct, 0.41, 0.43);
		Assert.InRange(p.Quota.Weekly.Pct, 0.17, 0.19);
		Assert.Null(p.Quota.Weekly.ResetsAt);
		Assert.Equal(Pushed.ToString("o"), p.Quota.PolledAt);
	}

	[Fact]
	public void Serializes_with_snake_case_contract_keys()
	{
		var p = WidgetSnapshotBuilder.Build(new[] { Session("abc", 0.8) }, null, Pushed);
		var json = JsonSerializer.Serialize(p);
		Assert.Contains("\"session_id\":\"abc\"", json);
		Assert.Contains("\"context_tokens\":", json);
		Assert.Contains("\"is_error\":false", json);
		Assert.Contains("\"pushed_at\":", json);
		Assert.Contains("\"quota\":null", json);
	}

	[Fact]
	public void Serializes_name_and_name_source()
	{
		var named = new SessionModel("label", null, 160_000, 200_000, "claude-opus-4-8", SessionStatus.Live,
			Pushed.UtcDateTime, IsError: false, SessionId: "abc", Name: "Pairing", NameSource: "custom-title");
		var p = WidgetSnapshotBuilder.Build(new[] { named }, null, Pushed);
		var json = JsonSerializer.Serialize(p);
		Assert.Contains("\"name\":\"Pairing\"", json);
		Assert.Contains("\"name_source\":\"custom-title\"", json);
	}

	[Fact]
	public void Serializes_title_state()
	{
		var p = WidgetSnapshotBuilder.Build(new[] { Session("abc", 0.8), Session("def", 0.5) }, null, Pushed,
			titleStates: new Dictionary<string, string> { ["abc"] = "star" });
		var json = JsonSerializer.Serialize(p);
		Assert.Contains("\"title_state\":\"star\"", json);
		Assert.Contains("\"title_state\":null", json);
	}

	[Fact]
	public void Build_includes_antigravity_quota_groups()
	{
		var gGemini = new AntigravityQuotaGroup("Gemini Models", "desc", new[]
		{
			new AntigravityQuotaBucket("5h", 0.80, Pushed),
			new AntigravityQuotaBucket("weekly", 0.70, Pushed)
		});
		var gClaude = new AntigravityQuotaGroup("Claude and GPT models", "desc", new[]
		{
			new AntigravityQuotaBucket("5h", 0.90, Pushed),
			new AntigravityQuotaBucket("weekly", 0.50, Pushed)
		});
		var gUntouched = new AntigravityQuotaGroup("Untouched", "desc", new[]
		{
			new AntigravityQuotaBucket("5h", 1.0, Pushed),
			new AntigravityQuotaBucket("weekly", 1.0, Pushed)
		});

		var p = WidgetSnapshotBuilder.Build(Array.Empty<SessionModel>(), null, Pushed, agyGroups: new[] { gGemini, gUntouched, gClaude });
		Assert.NotNull(p.Quota);
		Assert.NotNull(p.Quota!.Antigravity);
		Assert.Equal(2, p.Quota.Antigravity!.Count);
		// GroupSortKey order: Claude (0) then Gemini (1)
		Assert.Equal("Claude and GPT models", p.Quota.Antigravity[0].DisplayName);
		Assert.Equal("Gemini Models", p.Quota.Antigravity[1].DisplayName);
		Assert.InRange(p.Quota.Antigravity[0].Session.Pct, 0.09, 0.11); // used = 1 - 0.90 = 0.10
		Assert.InRange(p.Quota.Antigravity[0].Weekly.Pct, 0.49, 0.51);  // used = 1 - 0.50 = 0.50

		var json = JsonSerializer.Serialize(p);
		Assert.Contains("\"antigravity\":[", json);
		Assert.Contains("\"display_name\":\"Claude and GPT models\"", json);
	}
}
