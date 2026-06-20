package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.ConversationSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PredecessorTitleTest {

	private fun row(id: String, title: String = "T", continuedFrom: String? = null) =
		ConversationRow(
			summary = ConversationSummary(
				id = id,
				title = title,
				state = "active",
				members = emptyList(),
				lastActivityAt = "",
				continuedFrom = continuedFrom,
			),
		)

	private fun rowsOf(vararg rows: ConversationRow): Map<String, ConversationRow> =
		rows.associateBy { it.id }

	@Test
	fun `no continued_from pointer yields no predecessor title`() {
		val current = row("conv-2", continuedFrom = null)
		assertNull(predecessorTitle(current, rowsOf(current)))
	}

	@Test
	fun `predecessor present in rows yields its title`() {
		val predecessor = row("conv-1", title = "Original work")
		val current = row("conv-2", continuedFrom = "conv-1")
		assertEquals("Original work", predecessorTitle(current, rowsOf(predecessor, current)))
	}

	@Test
	fun `predecessor absent from rows yields null so the banner is hidden`() {
		val current = row("conv-2", continuedFrom = "conv-gone")
		assertNull(predecessorTitle(current, rowsOf(current)))
	}
}
