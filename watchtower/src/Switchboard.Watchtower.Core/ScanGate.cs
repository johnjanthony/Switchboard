namespace Switchboard.Watchtower.Core;

/// Single-flight gate for the background scan. `TryEnter` admits one scan at a
/// time, but a scan that never calls `Exit` (e.g. a wedged \\wsl.localhost SMB
/// stall, which has no timeout) is superseded once its entry is older than the
/// expiry, so scanning can never freeze permanently. A superseded scan may
/// briefly overlap the fresh one (last-writer-wins on the UI; the orphaned
/// thread is harmless).
public sealed class ScanGate
{
	readonly TimeSpan _expiry;
	readonly object _lock = new();
	DateTime? _enteredUtc;

	public ScanGate(TimeSpan expiry) { _expiry = expiry; }

	public bool TryEnter(DateTime nowUtc)
	{
		lock (_lock)
		{
			if (_enteredUtc is DateTime e && nowUtc - e < _expiry) return false;  // in-flight, not yet stale
			_enteredUtc = nowUtc;                                                 // free, or supersede a wedged scan
			return true;
		}
	}

	public void Exit() { lock (_lock) { _enteredUtc = null; } }
}
