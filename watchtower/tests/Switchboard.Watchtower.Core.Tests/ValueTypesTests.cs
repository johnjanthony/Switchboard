using Switchboard.Watchtower.Core;
using Xunit;

public class ValueTypesTests
{
	[Fact]
	public void ContextTokens_sums_input_and_both_cache_fields_excludes_output()
	{
		var u = new Usage(10, 500_000, 300_000, 999);
		Assert.Equal(800_010, u.ContextTokens);
	}

	[Theory]
	[InlineData("claude-opus-4-8[1m]", 1_000_000)]
	[InlineData("claude-opus-4-7", 1_000_000)]
	[InlineData("claude-fable-5", 1_000_000)]
	[InlineData("Gemini 3.1 Pro (High)", 1_000_000)]
	[InlineData("Gemini 3.6 Flash (High)", 1_000_000)]
	[InlineData("claude-sonnet-4-6", 200_000)]
	[InlineData(null, 200_000)]
	[InlineData("", 200_000)]
	public void WindowFor_maps_opus_and_1m_to_one_million_else_default(string? model, long expected)
	{
		Assert.Equal(expected, ModelWindowMap.WindowFor(model));
	}

	[Theory]
	[InlineData("claude-opus-4-7", 641_000, 1_000_000)]    // opus base = 1M
	[InlineData("claude-opus-4-7", 50_000, 1_000_000)]     // opus stays 1M even when small
	[InlineData("claude-sonnet-4-6", 150_000, 200_000)]    // sonnet small → 200K
	[InlineData("claude-sonnet-4-6", 300_000, 1_000_000)]  // floor: context > 200K bumps to 1M
	[InlineData(null, 641_000, 1_000_000)]                 // floor saves unknown model
	public void EffectiveWindow_floors_window_up_to_fit_observed_context(string? model, long contextTokens, long expected)
	{
		Assert.Equal(expected, ModelWindowMap.EffectiveWindow(model, contextTokens));
	}

	[Theory]
	[InlineData(0.10, Severity.Green)]
	[InlineData(0.49, Severity.Green)]
	[InlineData(0.50, Severity.Amber)]
	[InlineData(0.80, Severity.Amber)]
	[InlineData(0.81, Severity.Red)]
	public void SeverityClassifier_uses_50_and_80_bands(double pct, Severity expected)
	{
		Assert.Equal(expected, SeverityClassifier.For(pct));
	}
}
