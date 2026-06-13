package io.github.johnjanthony.switchboard

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SelectionPolicyTest {
	@Test
	fun `phone default never auto-selects, even when nothing is selected`() {
		assertFalse(shouldAutoSelectOnMessageArrival(false, null, rowHidden = false, rowState = "active"))
	}

	@Test
	fun `wear auto-selects a visible active conversation when nothing is selected`() {
		assertTrue(shouldAutoSelectOnMessageArrival(true, null, rowHidden = false, rowState = "active"))
	}

	@Test
	fun `wear does not steal an existing selection`() {
		assertFalse(shouldAutoSelectOnMessageArrival(true, "conv-9", rowHidden = false, rowState = "active"))
	}

	@Test
	fun `wear skips hidden conversations`() {
		assertFalse(shouldAutoSelectOnMessageArrival(true, null, rowHidden = true, rowState = "active"))
	}

	@Test
	fun `wear skips ended conversations`() {
		assertFalse(shouldAutoSelectOnMessageArrival(true, null, rowHidden = false, rowState = "ended"))
	}
}
