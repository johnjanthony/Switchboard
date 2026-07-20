package io.github.johnjanthony.switchboard

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RejectedToastTrackerTest {
	private val attach = "2026-07-10T12:00:00Z"

	@Test
	fun `non-rejected never toasts`() {
		assertFalse(RejectedToastTracker().shouldToast("m1", false, "2026-07-10T12:00:01Z", attach))
	}

	@Test
	fun `rejected after attach toasts exactly once`() {
		val t = RejectedToastTracker()
		assertTrue(t.shouldToast("m1", true, "2026-07-10T12:00:01Z", attach))
		assertFalse(t.shouldToast("m1", true, "2026-07-10T12:00:01Z", attach))
	}

	@Test
	fun `rejected from before attach is history and never toasts`() {
		val t = RejectedToastTracker()
		assertFalse(t.shouldToast("m1", true, "2026-07-10T11:59:59Z", attach))
		// Marked seen: even a later same-id delivery stays silent.
		assertFalse(t.shouldToast("m1", true, "2026-07-10T12:00:05Z", attach))
	}

	@Test
	fun `null or blank timestamp is treated as history`() {
		val t = RejectedToastTracker()
		assertFalse(t.shouldToast("m1", true, null, attach))
		assertFalse(t.shouldToast("m2", true, "", attach))
	}

	@Test
	fun `distinct rejected ids after attach each toast`() {
		val t = RejectedToastTracker()
		assertTrue(t.shouldToast("m1", true, "2026-07-10T12:00:01Z", attach))
		assertTrue(t.shouldToast("m2", true, "2026-07-10T12:00:02Z", attach))
	}
}
