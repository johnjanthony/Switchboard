package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PendingReconcilerTest {
	private fun msg(attachedTo: String? = null) =
		ChannelMessage(sender = "s", type = "notify", text = "t", attached_to_msg_id = attachedTo)

	@Test
	fun `answered set names each target that has a reply attached`() {
		val messages = listOf(
			"q1" to msg(),
			"r1" to msg(attachedTo = "q1"),
			"q2" to msg(),
		)
		assertEquals(setOf("q1"), answeredQuestionMsgIds(messages))
	}

	@Test
	fun `attachment to an absent target does not mark anything answered`() {
		val messages = listOf("a" to msg(attachedTo = "missing"))
		assertTrue(answeredQuestionMsgIds(messages).isEmpty())
	}

	@Test
	fun `pendingFromNode builds a Pending from present fields`() {
		val p = pendingFromNode("req-1", "Claude", "Proceed?", false, "m1", listOf("Yes"))
		assertEquals("req-1", p?.requestId)
		assertEquals("Claude", p?.sender)
		assertEquals("m1", p?.msgId)
	}

	@Test
	fun `pendingFromNode tolerates a null msgId`() {
		val p = pendingFromNode("req-1", "Claude", "Proceed?", false, null, null)
		assertNull(p?.msgId)
		assertEquals("req-1", p?.requestId)
	}

	@Test
	fun `pendingFromNode returns null when a required field is missing`() {
		assertNull(pendingFromNode("req-1", null, "Proceed?", false, "m1", null))
		assertNull(pendingFromNode("req-1", "Claude", null, false, "m1", null))
	}
}
