using Switchboard.Watchtower.Core;
using Xunit;

public class SessionsViewTests
{
	static readonly DateTime Now = new(2026, 10, 11, 12, 0, 0, DateTimeKind.Utc);

	static StatsSession Item(long? tokens = 412000) =>
		new("s1", "Fix undo", "C:/NoSuchRoot/alpha/beta", "windows", "active", "Opus 5.5 high", "high", tokens, 1000000);

	static List<SessionModel> Poll(DateTime at, params StatsSession[] items) => StatsSessions.ToModels(items, at);

	[Fact]
	public void The_first_poll_draws_even_with_no_sessions() =>
		Assert.True(new SessionsView().Update(Poll(Now), light: false));

	[Fact]
	public void A_repeat_poll_of_the_same_sessions_does_not_redraw()
	{
		var view = new SessionsView();
		view.Update(Poll(Now, Item()), light: false);
		Assert.False(view.Update(Poll(Now.AddSeconds(4), Item()), light: false));
	}

	[Fact]
	public void A_skipped_poll_still_becomes_the_latest_so_the_cache_stays_dated()
	{
		var view = new SessionsView();
		view.Update(Poll(Now, Item()), light: false);
		view.Update(Poll(Now.AddMinutes(10), Item()), light: false);
		Assert.Equal(Now.AddMinutes(10), Assert.Single(view.Latest).LastActiveUtc);
	}

	[Fact]
	public void A_moved_context_redraws()
	{
		var view = new SessionsView();
		view.Update(Poll(Now, Item()), light: false);
		Assert.True(view.Update(Poll(Now.AddSeconds(4), Item(tokens: 500000)), light: false));
	}

	[Fact]
	public void A_session_that_appears_or_goes_redraws()
	{
		var view = new SessionsView();
		view.Update(Poll(Now, Item()), light: false);
		Assert.True(view.Update(Poll(Now.AddSeconds(4)), light: false));
		Assert.True(view.Update(Poll(Now.AddSeconds(8), Item()), light: false));
	}

	[Fact]
	public void A_taskbar_theme_flip_redraws_the_same_sessions()
	{
		var view = new SessionsView();
		view.Update(Poll(Now, Item()), light: false);
		Assert.True(view.Update(Poll(Now.AddSeconds(4), Item()), light: true));
	}
}
