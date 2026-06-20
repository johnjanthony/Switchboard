namespace Switchboard.Watchtower.Core;

public interface IDistroLister
{
	IReadOnlyList<string> RunningDistros();
}
