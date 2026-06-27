package io.github.johnjanthony.switchboard.ui

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.text.Layout
import android.text.Spanned
import android.text.TextPaint
import android.text.style.LeadingMarginSpan
import android.text.style.LineHeightSpan
import android.text.style.MetricAffectingSpan
import io.noties.markwon.core.MarkwonTheme

/**
 * Drop-in replacement for Markwon 4.6.2 io.noties.markwon.core.spans.CodeBlockSpan that draws
 * a rounded-corner background instead of a plain rect.
 *
 * Text styling parity: like the stock CodeBlockSpan this is a MetricAffectingSpan that calls
 * MarkwonTheme.applyCodeBlockTextStyle in both updateMeasureState and updateDrawState, so the
 * monospace typeface and code text size are preserved exactly. It only sets the typeface / size /
 * (block) text color, leaving room for the SwitchboardSyntaxHighlight ForegroundColorSpans that
 * are layered on top of the same range (those win because per-character spans applied later in the
 * Spanned override the block span's color on the chars they cover).
 *
 * Leading-margin parity: getLeadingMargin returns theme.getCodeBlockMargin(), the same value the
 * stock span returns, so the ~12dp left text inset is preserved with no double margin.
 *
 * Corner rounding: drawLeadingMargin is invoked once per visual line of the block. We detect the
 * first line via the `first` flag and the last line by comparing the line's text `end` against the
 * span's end offset in the Spanned. Only the block's outer corners are rounded; interior line
 * boundaries are square. To make a per-line rounded rect look like one continuous card, we draw
 * each line's rect with drawRoundRect but push the squared-off edge(s) outward by the corner
 * radius so the rounded arc falls outside the line's own band and the straight side abuts the
 * neighbouring line seamlessly.
 */
class RoundedCodeBlockSpan(
	private val theme: MarkwonTheme,
	private val backgroundColor: Int,
	private val cornerRadiusPx: Float,
) : MetricAffectingSpan(), LeadingMarginSpan, LineHeightSpan {

	private val rect = RectF()
	private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG)

	override fun updateMeasureState(p: TextPaint) {
		theme.applyCodeBlockTextStyle(p)
	}

	override fun updateDrawState(ds: TextPaint) {
		theme.applyCodeBlockTextStyle(ds)
	}

	override fun getLeadingMargin(first: Boolean): Int {
		return theme.getCodeBlockMargin()
	}

	// Monospace code lines pack tightly; Markwon adds no code line spacing, so a multi-line block
	// crowds (and can overlap). Add ~30% leading to code lines only (regular text is untouched).
	override fun chooseHeight(
		text: CharSequence?,
		start: Int,
		end: Int,
		spanstartv: Int,
		lineHeight: Int,
		fm: Paint.FontMetricsInt?,
	) {
		val m = fm ?: return
		val extra = ((m.descent - m.ascent) * 0.35f).toInt()
		m.descent += extra
		m.bottom += extra
	}

	override fun drawLeadingMargin(
		c: Canvas,
		p: Paint,
		x: Int,
		dir: Int,
		top: Int,
		baseline: Int,
		bottom: Int,
		text: CharSequence,
		start: Int,
		end: Int,
		first: Boolean,
		layout: Layout?,
	) {
		// Match stock CodeBlockSpan: span the full content width on the appropriate side of x.
		val left: Int
		val right: Int
		if (dir > 0) {
			left = x
			right = c.width
		} else {
			left = x - c.width
			right = x
		}

		// Determine whether this is the last visual line of the span. The span end is the same
		// across all lines of the block; read it from the Spanned so we don't depend on the
		// per-line `end` (which is the line's own text end, not the span's).
		val spanEnd = if (text is Spanned) text.getSpanEnd(this) else end
		val isLast = end >= spanEnd

		val roundTop = first
		val roundBottom = isLast

		bgPaint.style = Paint.Style.FILL
		bgPaint.color = backgroundColor

		if (roundTop && roundBottom) {
			// Single line: round all four corners normally.
			rect.set(left.toFloat(), top.toFloat(), right.toFloat(), bottom.toFloat())
			c.drawRoundRect(rect, cornerRadiusPx, cornerRadiusPx, bgPaint)
			return
		}

		// Multi-line: push the squared edge(s) out by the radius so the rounded arcs land
		// outside this line's band and the straight edge merges with the adjacent line.
		val r = cornerRadiusPx
		val t = if (roundTop) top.toFloat() else top - r
		val b = if (roundBottom) bottom.toFloat() else bottom + r
		rect.set(left.toFloat(), t, right.toFloat(), b)
		c.drawRoundRect(rect, r, r, bgPaint)
	}
}
