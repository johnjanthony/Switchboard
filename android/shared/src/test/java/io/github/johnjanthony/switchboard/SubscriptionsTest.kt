package io.github.johnjanthony.switchboard

import org.junit.Assert.assertEquals
import org.junit.Test

class SubscriptionsTest {
	@Test
	fun `dispose invokes every registered unsub once`() {
		val subs = Subscriptions()
		var a = 0
		var b = 0
		subs.add { a++ }
		subs.add { b++ }
		subs.dispose()
		assertEquals(1, a)
		assertEquals(1, b)
	}

	@Test
	fun `dispose is idempotent`() {
		val subs = Subscriptions()
		var count = 0
		subs.add { count++ }
		subs.dispose()
		subs.dispose()
		assertEquals(1, count)
	}

	@Test
	fun `add after dispose invokes immediately so nothing leaks`() {
		val subs = Subscriptions()
		subs.dispose()
		var invoked = 0
		subs.add { invoked++ }
		assertEquals(1, invoked)
	}

	@Test
	fun `size reflects registered count before dispose`() {
		val subs = Subscriptions()
		subs.add { }
		subs.add { }
		assertEquals(2, subs.size)
	}
}
