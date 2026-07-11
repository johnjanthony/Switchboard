package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage
import org.junit.Assert.assertEquals
import org.junit.Test

class PendingMessageBufferTest {
	private fun msg(text: String) = ChannelMessage(sender = "s", type = "notify", text = text)

	@Test
	fun `drain returns buffered messages in arrival order then clears`() {
		val buf = PendingMessageBuffer()
		buf.buffer("c1", "a", msg("1"))
		buf.buffer("c1", "b", msg("2"))
		assertEquals(listOf("a", "b"), buf.drain("c1").map { it.first })
		assertEquals(emptyList<String>(), buf.drain("c1").map { it.first })
	}

	@Test
	fun `buffering the same msgId twice keeps one entry with the latest value`() {
		val buf = PendingMessageBuffer()
		buf.buffer("c1", "a", msg("old"))
		buf.buffer("c1", "a", msg("new"))
		val drained = buf.drain("c1")
		assertEquals(1, drained.size)
		assertEquals("new", drained[0].second.text)
	}

	@Test
	fun `per-conversation cap evicts oldest`() {
		val buf = PendingMessageBuffer(perConvCap = 2)
		buf.buffer("c1", "a", msg("1"))
		buf.buffer("c1", "b", msg("2"))
		buf.buffer("c1", "c", msg("3"))
		assertEquals(listOf("b", "c"), buf.drain("c1").map { it.first })
	}

	@Test
	fun `drain of unknown conversation is empty`() {
		assertEquals(emptyList<String>(), PendingMessageBuffer().drain("nope").map { it.first })
	}
}
