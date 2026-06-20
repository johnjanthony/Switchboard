using System.Drawing;
using Switchboard.Watchtower.Core;
using Xunit;

public class TaskbarPlacementTests
{
	// Taskbar on a secondary monitor (non-zero origin) so the parent-relative subtraction is actually exercised.
	static readonly Rectangle Taskbar = new(1920, 1040, 1920, 48); // left, top, width, height

	[Fact]
	public void Compute_fallback_centers_vertically_and_anchors_left_of_tray_in_screen_coords()
	{
		var p = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredScreenX: null, embedded: false);
		Assert.Equal(3500 - 90 - 8, p.X);        // anchored just left of the tray, screen X
		Assert.Equal(1040 + (48 - 34) / 2, p.Y); // vertically centered within the taskbar, screen Y
	}

	[Fact]
	public void Compute_embedded_subtracts_taskbar_origin_for_parent_relative_coords()
	{
		var p = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredScreenX: null, embedded: true);
		Assert.Equal((3500 - 90 - 8) - 1920, p.X);        // same anchor, now relative to the taskbar's left
		Assert.Equal((1040 + (48 - 34) / 2) - 1040, p.Y); // same anchor, now relative to the taskbar's top
	}

	[Theory]
	// preferred X (a dragged position, screen coords) wins over the tray anchor, clamped so the widget
	// stays left of the tray cluster (matches the CodeZeno monitor - it cannot be dragged over the clock).
	[InlineData(2500, false, 2500)]       // inside the taskbar, left of tray -> used as-is (screen)
	[InlineData(9999, false, 3410)]       // past the tray -> clamped to tray.Left - width (3500 - 90)
	[InlineData(0, false, 1920)]          // left of the taskbar -> clamped to the taskbar left
	[InlineData(2500, true, 2500 - 1920)] // embedded: clamp first, then convert to parent-relative
	public void Compute_uses_preferred_x_clamped_to_taskbar_bounds(int preferred, bool embedded, int expectedX)
	{
		var p = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredScreenX: preferred, embedded: embedded);
		Assert.Equal(expectedX, p.X);
	}
}
