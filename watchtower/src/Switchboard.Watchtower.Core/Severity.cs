namespace Switchboard.Watchtower.Core;

public enum Severity { Green, Amber, Red }

public static class SeverityClassifier
{
	// pct is 0..1. Green < amberAt, Amber up to redAt inclusive, Red above redAt.
	public static Severity For(double pct, double amberAt = 0.50, double redAt = 0.80)
		=> pct > redAt ? Severity.Red
		 : pct >= amberAt ? Severity.Amber
		 : Severity.Green;
}
