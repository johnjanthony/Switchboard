namespace Switchboard.Watchtower.Core;

// Backs off quota polling after an auth failure so the poll loop stops spawning
// quota-consuming headless Claude CLI turns while logged out. Pure and param-clocked
// like ScanGate: the caller passes the current time and the current credentials
// fingerprint; the gate holds the fingerprint recorded at the failure and refuses
// attempts until the credentials change (re-login rewrites the credentials file,
// changing the fingerprint) or the retry interval elapses (guards a transient
// server-side rejection of credentials that are actually fine).
public sealed class QuotaBackoff
{
	readonly TimeSpan _retryInterval;
	readonly object _lock = new();
	string? _failedFingerprint;   // fingerprint at the recorded auth failure; null = not backed off
	DateTime _lastFailureUtc;

	public QuotaBackoff(TimeSpan retryInterval) { _retryInterval = retryInterval; }

	// Derived from the credentials file content the poll already parses; never logged (contains the token).
	public static string Fingerprint(string token, long? expiresAtMs) => $"{token}|{expiresAtMs?.ToString() ?? "-"}";

	public bool IsBackedOff { get { lock (_lock) return _failedFingerprint is not null; } }

	// True when polling may spend spawns/HTTP: not backed off, credentials changed, or the retry is due.
	public bool ShouldAttempt(string fingerprint, DateTime nowUtc)
	{
		lock (_lock)
		{
			if (_failedFingerprint is null) return true;
			if (!string.Equals(fingerprint, _failedFingerprint, StringComparison.Ordinal)) return true;
			return nowUtc - _lastFailureUtc >= _retryInterval;
		}
	}

	public void RecordAuthFailure(string fingerprint, DateTime nowUtc)
	{
		lock (_lock) { _failedFingerprint = fingerprint; _lastFailureUtc = nowUtc; }
	}

	public void RecordSuccess() { lock (_lock) { _failedFingerprint = null; } }
}
