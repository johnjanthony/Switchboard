using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;

namespace Switchboard.Watchtower.Core;

/// <summary>One ring to draw: the ellipse rect for DrawEllipse/DrawArc, the clockwise sweep
/// in degrees from 12 o'clock, and the source values the view uses to pick the arc colour.</summary>
public readonly record struct ContextRing(RectangleF Bounds, float SweepDegrees, double Pct, bool IsError);

/// <summary>Ordered rings (outermost first) plus the count of sessions that did not get a ring.</summary>
public readonly record struct ContextRingLayoutResult(IReadOnlyList<ContextRing> Rings, int Overflow);

/// <summary>
/// Pure layout for the widget's nested context rings. Sorts sessions fullest-first (error sessions
/// rank ahead of all others), caps the visible count to what fits the strip, and computes each ring's
/// concentric bounding rect and sweep angle. No GDI drawing - the view consumes the result.
/// </summary>
public static class ContextRingLayout
{
	public static ContextRingLayoutResult Build(
		IReadOnlyList<SessionModel> sessions,
		float originX,
		float height,
		float thickness = 2.5f,
		float gap = 0.5f,
		int maxRings = 4)
	{
		if (sessions.Count == 0)
			return new ContextRingLayoutResult(Array.Empty<ContextRing>(), 0);

		float dMax = Math.Min(height - 8f, 28f);              // outer diameter, capped
		float penInset = thickness / 2f + 1f;                 // keep the outer stroke unclipped (matches RenderGauge)
		float step = thickness + gap;                         // radial distance between consecutive rings
		float od = dMax - 2f * penInset;                      // outer centreline-ellipse diameter
		float outerRadius = od / 2f;

		// How many concentric rings keep a bounding radius >= thickness (innermost never degenerate).
		int fitCount = outerRadius < thickness ? 0 : (int)Math.Floor((outerRadius - thickness) / step) + 1;
		int visible = Math.Min(Math.Min(sessions.Count, maxRings), fitCount);
		int overflow = sessions.Count - visible;

		float clusterTop = (height - dMax) / 2f;
		float ox = originX + penInset;
		float oy = clusterTop + penInset;

		// Stable sort: error sessions sort as "fullest" so an error always claims an outer ring.
		var ordered = sessions
			.OrderByDescending(s => s.IsError ? double.PositiveInfinity : Math.Clamp(s.Pct, 0, 1))
			.Take(visible)
			.ToList();

		var rings = new List<ContextRing>(ordered.Count);
		for (int i = 0; i < ordered.Count; i++)
		{
			var s = ordered[i];
			float inset = i * step;
			var bounds = new RectangleF(ox + inset, oy + inset, od - 2f * inset, od - 2f * inset);
			float sweep = s.IsError ? 360f : (float)(360.0 * Math.Clamp(s.Pct, 0, 1));
			rings.Add(new ContextRing(bounds, sweep, s.Pct, s.IsError));
		}

		return new ContextRingLayoutResult(rings, overflow);
	}
}
