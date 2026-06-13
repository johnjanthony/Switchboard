package io.github.johnjanthony.switchboard.network

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.OffsetDateTime
import java.time.ZoneOffset

class StaleSessionWarningTest {
	private fun member(sessionEndedAt: String?): ConversationMember =
		ConversationMember(
			cliSessionId = "sess-1",
			sender = "Claude",
			alive = false,
			sessionEndedAt = sessionEndedAt,
		)

	private fun summary(member: ConversationMember): ConversationSummary =
		ConversationSummary(
			id = "conv-1",
			title = "T",
			state = "active",
			members = listOf(member),
			lastActivityAt = "",
		)

	/**
	 * The server's exact format: datetime.now(timezone.utc).isoformat() ends in +00:00, not Z.
	 * Note: Instant.parse (the old implementation) also accepts +00:00 on JDK 17, so these
	 * tests verify window-boundary logic with the real server format, not parse robustness.
	 * Crash prevention (the M14 fix) is tested by `unparseable timestamp degrades to no warning`.
	 */
	private fun pythonIso(daysAgo: Long): String {
		val odt = OffsetDateTime.now(ZoneOffset.UTC).minusDays(daysAgo)
		return odt.toLocalDateTime().toString() + "+00:00"
	}

	@Test
	fun `server offset format 27 days ago is within the 25-29 day warning window`() {
		assertTrue(summary(member(pythonIso(27))).staleSessionWarning)
	}

	@Test
	fun `server offset format 5 days ago is outside the warning window`() {
		assertFalse(summary(member(pythonIso(5))).staleSessionWarning)
	}

	@Test
	fun `zulu format is also accepted`() {
		val iso = OffsetDateTime.now(ZoneOffset.UTC).minusDays(26).toInstant().toString()
		assertTrue(summary(member(iso)).staleSessionWarning)
	}

	@Test
	fun `unparseable timestamp degrades to no warning instead of throwing`() {
		assertFalse(summary(member("not-a-timestamp")).staleSessionWarning)
	}

	@Test
	fun `null sessionEndedAt does not warn`() {
		assertFalse(summary(member(null)).staleSessionWarning)
	}
}
