using System.Windows.Automation;

namespace Switchboard.Watchtower;

/// Enumerates Windows Terminal tab titles via UI Automation. WT-hosted sessions
/// only (VSCode terminals are invisible to UIA title enumeration - accepted
/// boundary); classification/correlation is pure Core (TabTitles).
internal static class TerminalTabScanner
{
	public static List<string> ReadTabTitles()
	{
		var titles = new List<string>();
		try
		{
			var condition = new PropertyCondition(AutomationElement.ClassNameProperty, "CASCADIA_HOSTING_WINDOW_CLASS");
			var windows = AutomationElement.RootElement.FindAll(TreeScope.Children, condition);
			foreach (AutomationElement window in windows)
			{
				var tabs = window.FindAll(TreeScope.Descendants,
					new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.TabItem));
				foreach (AutomationElement tab in tabs)
				{
					var name = tab.Current.Name;
					if (!string.IsNullOrWhiteSpace(name)) titles.Add(name);
				}
			}
		}
		catch
		{
			// Best-effort sensor: a UIA hiccup (window closing mid-walk, COM timeout)
			// must never take the widget down; an empty read just means no signal.
			return titles;
		}
		return titles;
	}
}
