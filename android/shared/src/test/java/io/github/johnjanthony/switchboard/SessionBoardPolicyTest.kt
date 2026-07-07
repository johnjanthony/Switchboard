package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.RegistrySession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionBoardPolicyTest {

	private fun rec(
		state: String = "idle",
		lastEventAt: String = "2026-07-07T12:00:00+00:00",
		name: String? = null,
		sender: String? = null,
		cwd: String = "C:\\Work\\Switchboard",
	) = RegistrySession(cliSessionId = "s1", cwd = cwd, state = state, lastEventAt = lastEventAt, name = name, sender = sender)

	// --- sessionBoardLabel ---

	@Test
	fun `label uses name when present`() {
		assertEquals("My Session", sessionBoardLabel(rec(name = "My Session", sender = "Claude Win")))
	}

	@Test
	fun `label falls back to sender when name is blank`() {
		assertEquals("Claude Win", sessionBoardLabel(rec(name = "   ", sender = "Claude Win")))
	}

	@Test
	fun `label falls back to cwd tail when name and sender are blank`() {
		assertEquals("Switchboard", sessionBoardLabel(rec(name = null, sender = "", cwd = "C:\\Work\\Switchboard")))
	}

	@Test
	fun `label falls back to unknown when name, sender, and cwd are all blank`() {
		assertEquals("(unknown)", sessionBoardLabel(rec(name = null, sender = null, cwd = "")))
	}

	// --- cwdTail ---

	@Test
	fun `cwdTail takes the last segment across backslashes`() {
		assertEquals("Switchboard", cwdTail("C:\\Work\\Switchboard"))
	}

	@Test
	fun `cwdTail takes the last segment across forward slashes`() {
		assertEquals("project", cwdTail("/home/user/project"))
	}

	@Test
	fun `cwdTail handles mixed separators`() {
		assertEquals("Sub", cwdTail("C:\\Work/Switchboard\\Sub"))
	}

	@Test
	fun `cwdTail ignores a trailing separator`() {
		assertEquals("Switchboard", cwdTail("C:\\Work\\Switchboard\\"))
	}

	@Test
	fun `cwdTail is empty for a blank cwd`() {
		assertEquals("", cwdTail(""))
	}

	// --- parseIsoMs ---

	@Test
	fun `parseIsoMs is null for a null or blank string`() {
		assertEquals(null, parseIsoMs(null))
		assertEquals(null, parseIsoMs(""))
		assertEquals(null, parseIsoMs("   "))
	}

	@Test
	fun `parseIsoMs is null for an unparseable string`() {
		assertEquals(null, parseIsoMs("not-a-date"))
	}

	@Test
	fun `parseIsoMs accepts the server plus-offset format and the client Z format`() {
		val fromServer = parseIsoMs("2026-07-07T12:00:00+00:00")
		val fromClient = parseIsoMs("2026-07-07T12:00:00Z")
		assertTrue(fromServer != null)
		// Both notations name the same UTC instant; parsing (not string comparison) proves it.
		assertEquals(fromServer, fromClient)
	}

	// --- sessionNeedsAttention ---

	@Test
	fun `needs attention when idle and never acked`() {
		assertTrue(sessionNeedsAttention(rec(state = "idle"), ackIso = null))
	}

	@Test
	fun `does not need attention when the ack is after the last event`() {
		val r = rec(state = "idle", lastEventAt = "2026-07-07T12:00:00+00:00")
		assertFalse(sessionNeedsAttention(r, ackIso = "2026-07-07T12:00:01Z"))
	}

	@Test
	fun `needs attention is true for a plus-offset event one second after a Z ack`() {
		// lastEventAt (server, +00:00) is genuinely one real second after ackIso (client nowIso, Z).
		// A naive lexicographic compare of the two raw strings happens to agree here (verified
		// empirically: for a real, nonzero time delta the offset suffix never becomes the
		// deciding character), but the contract requires parsing via parseIsoMs regardless -
		// this only becomes load-bearing at ties (equal instants sort unequal as raw strings) or
		// if a non-UTC offset ever entered the mix. This test guards the parsed-comparison path.
		val ackIso = "2026-07-07T12:00:00Z"
		val r = rec(state = "idle", lastEventAt = "2026-07-07T12:00:01+00:00")
		assertTrue(sessionNeedsAttention(r, ackIso))
	}

	@Test
	fun `does not need attention for a non-idle state`() {
		assertFalse(sessionNeedsAttention(rec(state = "active"), ackIso = null))
	}

	@Test
	fun `does not need attention when lastEventAt is unparseable`() {
		assertFalse(sessionNeedsAttention(rec(state = "idle", lastEventAt = "garbage"), ackIso = null))
	}

	// --- sessionWakeLabel ---

	@Test
	fun `wake label for awaiting_agent`() {
		assertEquals("wakes instantly", sessionWakeLabel(rec(state = "awaiting_agent")))
	}

	@Test
	fun `wake label for awaiting_human`() {
		assertEquals("wakes on next phone answer", sessionWakeLabel(rec(state = "awaiting_human")))
	}

	@Test
	fun `wake label for active`() {
		assertEquals("wakes at end of current turn", sessionWakeLabel(rec(state = "active")))
	}

	@Test
	fun `wake label for idle`() {
		assertEquals("wakes on your next prompt", sessionWakeLabel(rec(state = "idle")))
	}

	@Test
	fun `wake label for ended and lost`() {
		assertEquals("Resume into conversation", sessionWakeLabel(rec(state = "ended")))
		assertEquals("Resume into conversation", sessionWakeLabel(rec(state = "lost")))
	}

	@Test
	fun `wake label is blank for an unrecognized state`() {
		assertEquals("", sessionWakeLabel(rec(state = "weird")))
	}

	// --- isSessionSelectable / isSessionResumable ---

	@Test
	fun `live states are selectable but not resumable`() {
		for (state in listOf("active", "idle", "awaiting_human", "awaiting_agent")) {
			assertTrue("state=$state should be selectable", isSessionSelectable(rec(state = state)))
			assertFalse("state=$state should not be resumable", isSessionResumable(rec(state = state)))
		}
	}

	@Test
	fun `terminal states with a cwd are selectable and resumable`() {
		for (state in listOf("ended", "lost")) {
			val r = rec(state = state, cwd = "C:\\Work\\Switchboard")
			assertTrue("state=$state should be selectable", isSessionSelectable(r))
			assertTrue("state=$state should be resumable", isSessionResumable(r))
		}
	}

	@Test
	fun `terminal states without a cwd are neither selectable nor resumable`() {
		for (state in listOf("ended", "lost")) {
			val r = rec(state = state, cwd = "")
			assertFalse("state=$state should not be selectable", isSessionSelectable(r))
			assertFalse("state=$state should not be resumable", isSessionResumable(r))
		}
	}

	// --- partitionSessionBoard ---

	@Test
	fun `partition puts needs-attention live sessions first, then live by recency, ended sessions second`() {
		val attn = rec(state = "idle", lastEventAt = "2026-07-07T12:00:00+00:00")
		val recent = rec(state = "active", lastEventAt = "2026-07-07T13:00:00+00:00")
		val older = rec(state = "awaiting_human", lastEventAt = "2026-07-07T11:00:00+00:00")
		val garbage = rec(state = "idle", lastEventAt = "garbage", cwd = "C:\\Work\\Garbage")
		val endedNew = rec(state = "ended", lastEventAt = "2026-07-07T10:00:00+00:00", cwd = "C:\\Work\\A")
		val endedOld = rec(state = "lost", lastEventAt = "2026-07-07T09:00:00+00:00", cwd = "C:\\Work\\B")
		val endedGarbage = rec(state = "ended", lastEventAt = "garbage", cwd = "C:\\Work\\C")

		val sessions = mapOf(
			"s-recent" to recent,
			"s-older" to older,
			"s-attn" to attn,
			"s-garbage" to garbage,
			"s-ended-garbage" to endedGarbage,
			"s-ended-old" to endedOld,
			"s-ended-new" to endedNew,
		)

		val (live, ended) = partitionSessionBoard(sessions, acks = emptyMap())

		assertEquals(listOf(attn, recent, older, garbage), live)
		assertEquals(listOf(endedNew, endedOld, endedGarbage), ended)
	}

	@Test
	fun `partition honors acks - an acked idle session is not attention-first`() {
		val acked = rec(state = "idle", lastEventAt = "2026-07-07T12:00:00+00:00")
		val unacked = rec(state = "idle", lastEventAt = "2026-07-07T11:00:00+00:00")
		val sessions = mapOf("s-acked" to acked, "s-unacked" to unacked)
		val acks = mapOf("s-acked" to "2026-07-07T12:00:01Z")

		val (live, _) = partitionSessionBoard(sessions, acks)

		assertEquals(listOf(unacked, acked), live)
	}

	// --- sessionBadgeCount ---

	@Test
	fun `badge count is the number of needs-attention sessions`() {
		val sessions = mapOf(
			"s1" to rec(state = "idle", lastEventAt = "2026-07-07T12:00:00+00:00"),
			"s2" to rec(state = "idle", lastEventAt = "2026-07-07T12:00:00+00:00"),
			"s3" to rec(state = "active", lastEventAt = "2026-07-07T12:00:00+00:00"),
		)
		val acks = mapOf("s2" to "2026-07-07T12:00:01Z")

		assertEquals(1, sessionBadgeCount(sessions, acks))
	}

	// --- conversationResumable ---

	private fun member(cliSessionId: String) = ConversationMember(cliSessionId = cliSessionId)

	@Test
	fun `conversation is resumable when a member's session is terminal`() {
		val members = listOf(member("a"), member("b"))
		val sessions = mapOf("a" to rec(state = "active"), "b" to rec(state = "ended", cwd = "C:\\Work\\X"))
		assertTrue(conversationResumable(members, sessions))
	}

	@Test
	fun `conversation is not resumable when no member's session is terminal`() {
		val members = listOf(member("a"), member("b"))
		val sessions = mapOf("a" to rec(state = "active"), "b" to rec(state = "idle"))
		assertFalse(conversationResumable(members, sessions))
	}

	@Test
	fun `conversation is not resumable when a member has no registry record`() {
		val members = listOf(member("missing"))
		val sessions = mapOf("a" to rec(state = "ended", cwd = "C:\\Work\\X"))
		assertFalse(conversationResumable(members, sessions))
	}
}
