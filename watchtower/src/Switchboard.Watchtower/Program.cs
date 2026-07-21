using Switchboard.Watchtower.Core;

namespace Switchboard.Watchtower;

internal static class Program
{
	[STAThread]
	static void Main()
	{
		try
		{
			using var mutex = new Mutex(initiallyOwned: true, "Switchboard.Watchtower.SingleInstance", out bool isNew);
			if (!isNew)
			{
				try
				{
					if (!mutex.WaitOne(TimeSpan.Zero, false))
					{
						WatchtowerLog.Info("mutex", "already running or mutex held; exiting new instance");
						return;
					}
				}
				catch (AbandonedMutexException) { }
			}

			ApplicationConfiguration.Initialize();
			Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
			Application.ThreadException += (_, e) => WatchtowerLog.Error("ui-thread", e.Exception);
			AppDomain.CurrentDomain.UnhandledException += (_, e) =>
			{
				if (e.ExceptionObject is Exception ex) WatchtowerLog.Error("appdomain", ex);
			};

			WatchtowerLog.Info("startup", "starting Watchtower host");
			var config = AppConfig.Load();
			using var host = new AppHost(config);
			host.Start();
			Application.Run();
		}
		catch (Exception ex)
		{
			WatchtowerLog.Error("startup-main", ex);
		}
	}
}
