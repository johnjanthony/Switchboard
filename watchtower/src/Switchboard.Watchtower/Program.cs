using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal static class Program
{
	[STAThread]
	static void Main()
	{
		using var mutex = new Mutex(initiallyOwned: true, "Switchboard.Watchtower.SingleInstance", out bool isNew);
		if (!isNew) return; // already running

		ApplicationConfiguration.Initialize();
		Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
		Application.ThreadException += (_, e) => WatchtowerLog.Error("ui-thread", e.Exception);
		AppDomain.CurrentDomain.UnhandledException += (_, e) =>
		{
			if (e.ExceptionObject is Exception ex) WatchtowerLog.Error("appdomain", ex);
		};

		var config = AppConfig.Load();
		using var host = new AppHost(config);
		host.Start();
		Application.Run();
	}
}
