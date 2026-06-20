using Switchboard.Watchtower.Core;
using Xunit;

public class QuotaParserTests
{
	[Fact]
	public void ParseUsage_reads_five_hour_and_seven_day_utilization_and_reset()
	{
		var json = """
		{"five_hour":{"utilization":50,"resets_at":"2026-06-13T15:19:59.321598+00:00"},
		 "seven_day":{"utilization":31,"resets_at":"2026-06-16T12:59:59+00:00"}}
		""";
		var u = QuotaParser.ParseUsage(json);
		Assert.NotNull(u);
		Assert.Equal(50, u!.Value.Session.Percentage);
		Assert.Equal(31, u.Value.Weekly.Percentage);
		var expectedSession = new DateTimeOffset(2026, 6, 13, 15, 19, 59, TimeSpan.Zero);
		Assert.True((u.Value.Session.ResetsAt!.Value - expectedSession).Duration() < TimeSpan.FromSeconds(1));
		Assert.Equal(new DateTimeOffset(2026, 6, 16, 12, 59, 59, TimeSpan.Zero), u.Value.Weekly.ResetsAt!.Value);
	}

	[Fact]
	public void ParseCredentials_reads_oauth_token_and_expiry()
	{
		var json = """{"claudeAiOauth":{"accessToken":"sk-abc-123","expiresAt":1781000000000}}""";
		var c = QuotaParser.ParseCredentials(json);
		Assert.NotNull(c);
		Assert.Equal("sk-abc-123", c!.Value.Token);
		Assert.Equal(1781000000000L, c.Value.ExpiresAtMs);
	}

	[Theory]
	[InlineData(1000, 999, false)]  // expiry in the future
	[InlineData(1000, 1000, true)]  // exactly at expiry -> expired
	[InlineData(1000, 1500, true)]  // past expiry
	public void IsExpired_compares_now_against_expiry_ms(long expMs, long nowMs, bool expected)
	{
		Assert.Equal(expected, QuotaParser.IsExpired(expMs, DateTimeOffset.FromUnixTimeMilliseconds(nowMs)));
	}

	[Fact]
	public void IsExpired_is_false_when_expiry_unknown()
	{
		Assert.False(QuotaParser.IsExpired(null, DateTimeOffset.UtcNow));
	}
}
