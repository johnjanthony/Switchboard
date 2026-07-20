package io.github.johnjanthony.switchboard.fcm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
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
}
