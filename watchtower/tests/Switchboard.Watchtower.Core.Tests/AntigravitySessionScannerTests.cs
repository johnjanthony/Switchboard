using Switchboard.Watchtower.Core;
using Xunit;

public class AntigravitySessionScannerTests : IDisposable
{
	readonly string _tempDir;
	readonly string _cliBrain;
	readonly string _ideBrain;
	static readonly DateTime Now = new(2026, 7, 23, 12, 0, 0, DateTimeKind.Utc);

	public AntigravitySessionScannerTests()
	{
		_tempDir = Path.Combine(Path.GetTempPath(), "AntigravityScannerTest_" + Guid.NewGuid().ToString("N"));
		_cliBrain = Path.Combine(_tempDir, "cli", "brain");
		_ideBrain = Path.Combine(_tempDir, "ide", "brain");
		Directory.CreateDirectory(_cliBrain);
		Directory.CreateDirectory(_ideBrain);
	}

	public void Dispose()
	{
		try { Directory.Delete(_tempDir, recursive: true); } catch { }
	}

	[Fact]
	public void ActiveTranscripts_scans_brain_roots_filters_mtime_and_deduplicates()
	{
		string activeUuid = "11111111-1111-1111-1111-111111111111";
		string staleUuid = "22222222-2222-2222-2222-222222222222";
		string retainedUuid = "33333333-3333-3333-3333-333333333333";

		// Active session in CLI
		var activeFile = CreateTranscriptFile(_cliBrain, activeUuid, Now.AddMinutes(-2));

		// Stale session in CLI (10 min ago)
		CreateTranscriptFile(_cliBrain, staleUuid, Now.AddMinutes(-10));

		// Stale session in IDE but retained in retainIds
		var retainedFile = CreateTranscriptFile(_ideBrain, retainedUuid, Now.AddMinutes(-10));

		// Duplicate activeUuid in IDE (older mtime, should be deduplicated)
		CreateTranscriptFile(_ideBrain, activeUuid, Now.AddMinutes(-4));

		var roots = new[] { _cliBrain, _ideBrain };
		var retainSet = new HashSet<string> { retainedUuid };

		var result = AntigravitySessionScanner.ActiveTranscripts(roots, Now, activeWindowMinutes: 5, retainIds: retainSet).ToList();

		Assert.Equal(2, result.Count);
		Assert.Contains(activeFile, result);
		Assert.Contains(retainedFile, result);
	}

	string CreateTranscriptFile(string brainRoot, string uuid, DateTime mtimeUtc)
	{
		var dir = Path.Combine(brainRoot, uuid, ".system_generated", "logs");
		Directory.CreateDirectory(dir);
		var path = Path.Combine(dir, "transcript_full.jsonl");
		File.WriteAllText(path, "{\"step_index\":0}");
		File.SetLastWriteTimeUtc(path, mtimeUtc);
		return path;
	}
}
