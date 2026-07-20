namespace Switchboard.Watchtower.Core;

// Shared transcript-enumeration kernel for the session scanners. The enumeration is
// materialized INSIDE the try: a try around a lazy Directory.EnumerateFiles call alone
// catches nothing, because the directory handle opens at the first MoveNext. With the
// materialization under the try, a directory that dies mid-enumeration contributes
// nothing (skip that directory) instead of aborting the caller's whole scan and
// blanking every session until a later clean tick.
public static class SafeScan
{
	// Any failure here means the directory is unreadable; skipping it wholesale is
	// always better than losing the whole scan (matches the WSL glob's semantics).
	public static List<string> Materialize(Func<IEnumerable<string>> produce)
	{
		try { return produce().ToList(); }
		catch { return new List<string>(); }
	}

	// Pair each path with its mtime, skipping files whose read fails.
	public static IEnumerable<(string Path, DateTime MtimeUtc)> WithMtimes(
		IEnumerable<string> paths, Func<string, DateTime> mtimeOf)
	{
		foreach (var path in paths)
		{
			DateTime mtime;
			try { mtime = mtimeOf(path); }
			catch (IOException) { continue; }
			yield return (path, mtime);
		}
	}
}
