package io.github.johnjanthony.switchboard

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ParseFailureNoticeTest {
	@Test
	fun `no failures means no notice`() {
		assertNull(conversationParseFailureNotice(emptyMap()))
	}

	@Test
	fun `single failure names the conversation and points at logcat`() {
		val notice = conversationParseFailureNotice(mapOf("conv-1" to "DatabaseException: x"))
		assertTrue(notice!!.contains("1 conversation"))
		assertTrue(notice.contains("conv-1"))
		assertTrue(notice.contains("logcat"))
	}

	@Test
	fun `multiple failures list sorted ids`() {
		val notice = conversationParseFailureNotice(
			mapOf("conv-b" to "x", "conv-a" to "y"),
		)
		assertEquals(true, notice!!.indexOf("conv-a") < notice.indexOf("conv-b"))
		assertTrue(notice.contains("2 conversation"))
	}
}
