package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage
import org.junit.Assert.assertEquals
import org.junit.Test

class MessageOrderingTest {
	private fun msg(text: String, attachedTo: String? = null): ChannelMessage =
		ChannelMessage(
			sender = "test",
			type = "notify",
			text = text,
			attached_to_msg_id = attachedTo,
		)

	@Test
	fun `unattached messages keep arrival order`() {
		val input = listOf(
			"a" to msg("first"),
			"b" to msg("second"),
			"c" to msg("third"),
		)
		val out = applySpliceOrder(input)
		assertEquals(listOf("a", "b", "c"), out.map { it.first })
	}

	@Test
	fun `reply attached to first message splices immediately after`() {
		val input = listOf(
			"q1" to msg("question"),
			"m2" to msg("intervening message"),
			"r1" to msg("reply to q1", attachedTo = "q1"),
		)
		val out = applySpliceOrder(input)
		// Reply 'r1' must sit immediately after 'q1', BEFORE 'm2'.
		assertEquals(listOf("q1", "r1", "m2"), out.map { it.first })
	}

	@Test
	fun `reply that already arrives in correct position stays put`() {
		val input = listOf(
			"q1" to msg("question"),
			"r1" to msg("reply to q1", attachedTo = "q1"),
			"m2" to msg("later message"),
		)
		val out = applySpliceOrder(input)
		assertEquals(listOf("q1", "r1", "m2"), out.map { it.first })
	}

	@Test
	fun `multiple replies to same target preserve their arrival order`() {
		val input = listOf(
			"q1" to msg("question"),
			"r2" to msg("second reply", attachedTo = "q1"),
			"r1" to msg("first reply", attachedTo = "q1"),
		)
		val out = applySpliceOrder(input)
		// r2 arrived before r1 in arrival order; both attach to q1; r2 comes first.
		assertEquals(listOf("q1", "r2", "r1"), out.map { it.first })
	}

	@Test
	fun `reply with unknown target is appended at end (graceful degradation)`() {
		val input = listOf(
			"a" to msg("first"),
			"b" to msg("orphan reply", attachedTo = "nonexistent-id"),
			"c" to msg("third"),
		)
		val out = applySpliceOrder(input)
		// Orphan keeps its arrival-order position when no target exists.
		assertEquals(listOf("a", "b", "c"), out.map { it.first })
	}

	@Test
	fun `chained attachment is supported - reply to a reply attaches under its target`() {
		val input = listOf(
			"q1" to msg("question"),
			"r1" to msg("reply", attachedTo = "q1"),
			"r2" to msg("reply to reply", attachedTo = "r1"),
		)
		val out = applySpliceOrder(input)
		// r2 attaches to r1; r1 attaches to q1. After splicing r1 under q1,
		// r2 then attaches under r1.
		assertEquals(listOf("q1", "r1", "r2"), out.map { it.first })
	}
}
