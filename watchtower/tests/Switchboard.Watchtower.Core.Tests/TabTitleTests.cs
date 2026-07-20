using Switchboard.Watchtower.Core;
using Xunit;

public class TabTitleTests
{
	static SessionModel Named(string id, string? name) =>
		new("label", null, 1, 200_000, "m", SessionStatus.Live, DateTime.UtcNow, SessionId: id, Name: name);

	[Fact]
	public void Braille_spinner_classifies_working()
	{
		var (state, name) = TabTitles.Classify("⣾ My Task");
		Assert.Equal("working", state);
		Assert.Equal("My Task", name);
	}

	[Fact]
	public void Star_prefix_classifies_star()
	{
		var (state, name) = TabTitles.Classify("✳ OSC-Probe");
		Assert.Equal("star", state);
		Assert.Equal("OSC-Probe", name);
	}

	[Fact]
	public void Star_with_variation_selector_classifies_star()
	{
		var (state, name) = TabTitles.Classify("✳️ Named");
		Assert.Equal("star", state);
		Assert.Equal("Named", name);
	}

	[Fact]
	public void Plain_title_has_null_state_and_full_name()
	{
		var (state, name) = TabTitles.Classify("Claude Code");
		Assert.Null(state);
		Assert.Equal("Claude Code", name);
	}

	[Fact]
	public void Null_or_blank_title_is_null_null()
	{
		Assert.Equal((null, null), TabTitles.Classify(null));
		Assert.Equal((null, null), TabTitles.Classify("   "));
	}

	[Fact]
	public void Correlate_maps_unique_name_match()
	{
		var tabs = new[] { ((string?)"star", (string?)"Fixing FCM tests") };
		var sessions = new[] { Named("s1", "Fixing FCM tests"), Named("s2", "Other") };
		var map = TabTitles.Correlate(tabs, sessions);
		Assert.Equal("star", map["s1"]);
		Assert.False(map.ContainsKey("s2"));
	}

	[Fact]
	public void Correlate_drops_duplicate_session_names()
	{
		var tabs = new[] { ((string?)"working", (string?)"Dup") };
		var sessions = new[] { Named("s1", "Dup"), Named("s2", "Dup") };
		Assert.Empty(TabTitles.Correlate(tabs, sessions));
	}

	[Fact]
	public void Correlate_drops_duplicate_tab_names_and_stateless_tabs()
	{
		var tabs = new[] { ((string?)"star", (string?)"Dup"), ((string?)"working", (string?)"Dup"), ((string?)null, (string?)"Solo") };
		var sessions = new[] { Named("s1", "Dup"), Named("s2", "Solo") };
		Assert.Empty(TabTitles.Correlate(tabs, sessions));
	}
}
