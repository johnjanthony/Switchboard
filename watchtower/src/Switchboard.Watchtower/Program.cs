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

		var config = AppConfig.Load();
		using var host = new AppHost(config);
		host.Start();
		Application.Run();
	}
}
