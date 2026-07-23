using System;
using System.Linq;
using Switchboard.Watchtower.Core;
using Xunit;

namespace Switchboard.Watchtower.Core.Tests;

public class AntigravityQuotaTests
{
	// Live sample captured 2026-07-23 from RetrieveUserQuotaSummary.
	const string Sample = """
	{"response":{"groups":[
	{"displayName":"Gemini Models","description":"Models within this group: Gemini Flash, Gemini Pro","buckets":[
	{"bucketId":"gemini-weekly","displayName":"Weekly Limit","window":"weekly","remainingFraction":0.75635,"resetTime":"2026-07-28T17:47:29Z"},
	{"bucketId":"gemini-5h","displayName":"Five Hour Limit","window":"5h","remainingFraction":0.1844278,"resetTime":"2026-07-23T19:19:19Z"}]},
	{"displayName":"Claude and GPT models","description":"Models within this group: Claude Opus, Claude Sonnet, GPT-OSS","buckets":[
	{"bucketId":"3p-weekly","displayName":"Weekly Limit","window":"weekly","remainingFraction":0.6698312,"resetTime":"2026-07-30T17:40:03Z"},
	{"bucketId":"3p-5h","displayName":"Five Hour Limit","window":"5h","remainingFraction":0.0094936,"resetTime":"2026-07-23T22:40:03Z"}]}
	],"description":"Within each group, models share a weekly limit and a 5-hour limit."}}
	""";

	[Fact]
	public void Parse_ExtractsGroupsAndBuckets()
	{
		var s = AntigravityQuota.Parse(Sample);
		Assert.NotNull(s);
		Assert.Equal(2, s!.Groups.Count);
		Assert.Equal("Gemini Models", s.Groups[0].DisplayName);
		Assert.Equal(2, s.Groups[0].Buckets.Count);
		var weekly = AntigravityQuota.Bucket(s.Groups[0], "weekly");
		Assert.NotNull(weekly);
		Assert.Equal(0.75635, weekly!.RemainingFraction, 5);
		Assert.Equal(new DateTimeOffset(2026, 7, 28, 17, 47, 29, TimeSpan.Zero), weekly.ResetTimeUtc);
	}

	[Fact]
	public void UsedPercent_IsInverseOfRemaining()
	{
		var b = new AntigravityQuotaBucket("5h", 0.1844278, null);
		Assert.Equal(81.55722, AntigravityQuota.UsedPercent(b), 4);
	}

	[Fact]
	public void UsedPercent_ClampsOutOfRange()
	{
		Assert.Equal(0.0, AntigravityQuota.UsedPercent(new AntigravityQuotaBucket("5h", 1.5, null)));
		Assert.Equal(100.0, AntigravityQuota.UsedPercent(new AntigravityQuotaBucket("5h", -0.2, null)));
	}

	[Fact]
	public void IsGroupVisible_FalseWhenEveryBucketUntouched()
	{
		var g = new AntigravityQuotaGroup("G", null, new[]
		{
			new AntigravityQuotaBucket("weekly", 1.0, null),
			new AntigravityQuotaBucket("5h", 1.0, null),
		});
		Assert.False(AntigravityQuota.IsGroupVisible(g));
	}

	[Fact]
	public void IsGroupVisible_TrueWhenAnyBucketTouched()
	{
		var g = new AntigravityQuotaGroup("G", null, new[]
		{
			new AntigravityQuotaBucket("weekly", 1.0, null),
			new AntigravityQuotaBucket("5h", 0.18, null),
		});
		Assert.True(AntigravityQuota.IsGroupVisible(g));
	}

	[Fact]
	public void Parse_ReturnsNullOnMalformedJson()
	{
		Assert.Null(AntigravityQuota.Parse("{not json"));
	}

	[Fact]
	public void Parse_ToleratesMissingResetAndEmptyGroups()
	{
		var s = AntigravityQuota.Parse("""{"response":{"groups":[]}}""");
		Assert.NotNull(s);
		Assert.Empty(s!.Groups);
	}

	[Fact]
	public void Parse_ReadsRemainingFractionFromString()
	{
		var s = AntigravityQuota.Parse("""{"response":{"groups":[{"displayName":"G","buckets":[{"window":"5h","remainingFraction":"0.75","resetTime":"2026-07-23T19:19:19Z"}]}]}}""");
		Assert.NotNull(s);
		var b = AntigravityQuota.Bucket(s!.Groups[0], "5h");
		Assert.NotNull(b);
		Assert.Equal(0.75, b!.RemainingFraction, 5);
	}

	[Fact]
	public void GroupSortKey_ClaudeBeforeGeminiBeforeOther()
	{
		var buckets = System.Array.Empty<AntigravityQuotaBucket>();
		Assert.Equal(0, AntigravityQuota.GroupSortKey(new AntigravityQuotaGroup("Claude and GPT models", null, buckets)));
		Assert.Equal(1, AntigravityQuota.GroupSortKey(new AntigravityQuotaGroup("Gemini Models", null, buckets)));
		Assert.Equal(2, AntigravityQuota.GroupSortKey(new AntigravityQuotaGroup("Something Else", null, buckets)));
	}

	[Fact]
	public void ToUsedWindow_ConvertsRemainingToUsedAndDefaultsMissingBucket()
	{
		var g = new AntigravityQuotaGroup("G", null, new[]
		{
			new AntigravityQuotaBucket("5h", 0.25, new System.DateTimeOffset(2026, 7, 23, 19, 0, 0, System.TimeSpan.Zero)),
		});
		var w5 = AntigravityQuota.ToUsedWindow(g, "5h");
		Assert.Equal(75.0, w5.Percentage, 5);
		Assert.Equal(new System.DateTimeOffset(2026, 7, 23, 19, 0, 0, System.TimeSpan.Zero), w5.ResetsAt);
		var wMissing = AntigravityQuota.ToUsedWindow(g, "weekly");
		Assert.Equal(0.0, wMissing.Percentage);
		Assert.Null(wMissing.ResetsAt);
	}
}
