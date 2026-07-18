using Switchboard.Watchtower.Core;
using Xunit;

public class QuotaBackoffTests
{
	static readonly DateTime Now = new(2026, 7, 15, 12, 0, 0, DateTimeKind.Utc);
	static readonly TimeSpan Day = TimeSpan.FromHours(24);

	[Fact]
	public void Fresh_gate_allows_attempts()
	{
		var gate = new QuotaBackoff(Day);
		Assert.True(gate.ShouldAttempt("tok|1", Now));
		Assert.False(gate.IsBackedOff);
	}

	[Fact]
	public void Auth_failure_blocks_same_fingerprint_within_interval()
	{
		var gate = new QuotaBackoff(Day);
		gate.RecordAuthFailure("tok|1", Now);
		Assert.True(gate.IsBackedOff);
		Assert.False(gate.ShouldAttempt("tok|1", Now.AddMinutes(1)));
		Assert.False(gate.ShouldAttempt("tok|1", Now.AddHours(23)));
	}

	[Fact]
	public void Changed_fingerprint_allows_immediately()
	{
		var gate = new QuotaBackoff(Day);
		gate.RecordAuthFailure("tok|1", Now);
		Assert.True(gate.ShouldAttempt("newtok|2", Now.AddMinutes(1)));
	}

	[Fact]
	public void Retry_interval_elapsing_allows_same_fingerprint()
	{
		var gate = new QuotaBackoff(Day);
		gate.RecordAuthFailure("tok|1", Now);
		Assert.True(gate.ShouldAttempt("tok|1", Now.AddHours(24)));
	}

	[Fact]
	public void Refailure_rearms_the_interval()
	{
		var gate = new QuotaBackoff(Day);
		gate.RecordAuthFailure("tok|1", Now);
		gate.RecordAuthFailure("tok|1", Now.AddHours(24));   // the 24h retry failed again
		Assert.False(gate.ShouldAttempt("tok|1", Now.AddHours(25)));
		Assert.True(gate.ShouldAttempt("tok|1", Now.AddHours(48)));
	}

	[Fact]
	public void Success_clears_the_backoff()
	{
		var gate = new QuotaBackoff(Day);
		gate.RecordAuthFailure("tok|1", Now);
		gate.RecordSuccess();
		Assert.False(gate.IsBackedOff);
		Assert.True(gate.ShouldAttempt("tok|1", Now.AddMinutes(1)));
	}

	[Fact]
	public void Fingerprint_distinguishes_token_and_expiry()
	{
		Assert.NotEqual(QuotaBackoff.Fingerprint("a", 1), QuotaBackoff.Fingerprint("b", 1));
		Assert.NotEqual(QuotaBackoff.Fingerprint("a", 1), QuotaBackoff.Fingerprint("a", 2));
		Assert.NotEqual(QuotaBackoff.Fingerprint("a", null), QuotaBackoff.Fingerprint("a", 1));
		Assert.Equal(QuotaBackoff.Fingerprint("a", 1), QuotaBackoff.Fingerprint("a", 1));
	}
}
