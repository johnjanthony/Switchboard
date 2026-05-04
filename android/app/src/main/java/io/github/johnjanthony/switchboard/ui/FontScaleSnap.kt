package io.github.johnjanthony.switchboard.ui

/** Inclusive minimum scale. The default size is the calibrated comfortable size; shrinking has no use case. */
const val MIN_FONT_SCALE = 1.0f

/** Inclusive maximum scale. Beyond this, the bubble layout starts to feel cartoonish. */
const val MAX_FONT_SCALE = 2.5f

/** Snap granularity. At a 14sp base, 0.05 maps to ~0.7sp differences — visually continuous. */
const val FONT_SCALE_STEP = 0.05f

/**
 * Clamp [raw] into [[MIN_FONT_SCALE], [MAX_FONT_SCALE]] and snap to the nearest [FONT_SCALE_STEP].
 *
 * Uses the standard "+0.5 then floor" trick for rounding. Behavior at exact midpoints is
 * undefined due to IEEE-754 float precision, but pinch gestures produce continuous values
 * that effectively never hit a midpoint.
 */
fun snapFontScale(raw: Float): Float {
	val clamped = raw.coerceIn(MIN_FONT_SCALE, MAX_FONT_SCALE)
	val steps = ((clamped - MIN_FONT_SCALE) / FONT_SCALE_STEP + 0.5f).toInt()
	return MIN_FONT_SCALE + steps * FONT_SCALE_STEP
}
