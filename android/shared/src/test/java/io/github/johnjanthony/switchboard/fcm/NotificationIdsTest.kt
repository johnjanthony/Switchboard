package io.github.johnjanthony.switchboard.fcm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NotificationIdsTest {
	@Test
	fun `same message id yields the same notification id across calls`() {
		assertEquals(notificationIdFor("-OQabc123", "body"), notificationIdFor("-OQabc123", "other body"))
	}

	@Test
	fun `distinct message ids yield distinct notification ids`() {
		assertNotEquals(notificationIdFor("-OQabc123", "b"), notificationIdFor("-OQabc124", "b"))
	}

	@Test
	fun `null message id falls back to the fallback text deterministically`() {
		assertEquals(notificationIdFor(null, "same body"), notificationIdFor(null, "same body"))
		assertNotEquals(notificationIdFor(null, "body one"), notificationIdFor(null, "body two"))
	}

	@Test
	fun `a dismiss targets the id its question was posted under`() {
		val data = mapOf("sb_message_type" to "dismiss", "message_id" to "-OQabc123", "conv_id" to "conv-1")
		assertEquals(notificationIdFor("-OQabc123", "Question body"), dismissalNotificationId(data))
	}

	@Test
	fun `a question payload is not a dismiss`() {
		val data = mapOf("sb_message_type" to "question", "message_id" to "-OQabc123", "body" to "Deploy?")
		assertNull(dismissalNotificationId(data))
	}

	@Test
	fun `a dismiss without a message id cancels nothing`() {
		assertNull(dismissalNotificationId(mapOf("sb_message_type" to "dismiss", "conv_id" to "conv-1")))
	}
}
