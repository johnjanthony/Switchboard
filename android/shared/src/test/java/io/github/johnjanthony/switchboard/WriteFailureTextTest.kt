package io.github.johnjanthony.switchboard

import org.junit.Assert.assertEquals
import org.junit.Test

class WriteFailureTextTest {
	@Test
	fun `formats label with error detail`() {
		assertEquals("Write failed: send reply (Permission denied)", writeFailureToastText("send reply", "Permission denied"))
	}

	@Test
	fun `formats label without detail when error message is null`() {
		assertEquals("Write failed: spawn", writeFailureToastText("spawn", null))
	}
}
