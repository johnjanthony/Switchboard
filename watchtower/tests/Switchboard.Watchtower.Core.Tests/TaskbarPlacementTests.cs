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
		var p = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredRightX: null, embedded: false);
		Assert.Equal(3500 - 90 - 8, p.X);        // anchored just left of the tray, screen X
		Assert.Equal(1040 + (48 - 34) / 2, p.Y); // vertically centered within the taskbar, screen Y
	}

	[Fact]
	public void Compute_embedded_subtracts_taskbar_origin_for_parent_relative_coords()
	{
		var p = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredRightX: null, embedded: true);
		Assert.Equal((3500 - 90 - 8) - 1920, p.X);        // same anchor, now relative to the taskbar's left
		Assert.Equal((1040 + (48 - 34) / 2) - 1040, p.Y); // same anchor, now relative to the taskbar's top
	}

	[Theory]
	// preferredRightX (a dragged position, screen coords) wins over the tray anchor, clamped so the widget's
	// right edge stays left of the tray cluster and left edge stays within the taskbar.
	[InlineData(2500, false, 2500 - 90)]             // inside the taskbar -> right edge is 2500, X is 2410 (screen)
	[InlineData(9999, false, 3500 - 90)]             // past the tray -> clamped to tray.Left (3500 - 90 = 3410)
	[InlineData(0, false, 1920)]                     // left of taskbar -> clamped so left edge stays on taskbar (1920)
	[InlineData(2500, true, (2500 - 90) - 1920)]     // embedded: clamp first, then convert to parent-relative
	public void Compute_uses_preferred_right_x_clamped_to_taskbar_bounds(int preferredRight, bool embedded, int expectedX)
	{
		var p = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredRightX: preferredRight, embedded: embedded);
		Assert.Equal(expectedX, p.X);
	}

	[Fact]
	public void Compute_expands_to_left_when_widget_width_increases()
	{
		// With right edge fixed at preferredRightX = 3000, increasing width from 90 to 200 moves X left (from 2910 to 2800).
		var p1 = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 90, widgetHeight: 34, preferredRightX: 3000, embedded: false);
		var p2 = TaskbarPlacement.Compute(Taskbar, trayLeft: 3500, widgetWidth: 200, widgetHeight: 34, preferredRightX: 3000, embedded: false);

		Assert.Equal(2910, p1.X);
		Assert.Equal(2800, p2.X);
		Assert.Equal(p1.X + 90, p2.X + 200); // Right edge stays anchored at 3000
	}
}
