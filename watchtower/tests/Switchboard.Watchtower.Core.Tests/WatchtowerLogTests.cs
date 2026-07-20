using System;
using System.IO;
using Switchboard.Watchtower.Core;
using Xunit;

public class WatchtowerLogTests
{
	[Fact]
	public void Error_appends_source_type_and_message()
	{
		var path = Path.Combine(Path.GetTempPath(), "wt-log-" + Guid.NewGuid().ToString("N") + ".txt");
		try
		{
			WatchtowerLog.Error("unit", new InvalidOperationException("boom"), path);
			var text = File.ReadAllText(path);
			Assert.Contains("[unit]", text);
			Assert.Contains("InvalidOperationException", text);
			Assert.Contains("boom", text);
		}
		finally { if (File.Exists(path)) File.Delete(path); }
	}

	[Fact]
	public void Logging_never_throws_when_the_path_is_unwritable()
	{
		// A directory path is not an appendable file; the failure must be swallowed.
		var dir = Path.Combine(Path.GetTempPath(), "wt-log-dir-" + Guid.NewGuid().ToString("N"));
		Directory.CreateDirectory(dir);
		try { Assert.Null(Record.Exception(() => WatchtowerLog.Info("unit", "hello", dir))); }
		finally { Directory.Delete(dir); }
	}
}
