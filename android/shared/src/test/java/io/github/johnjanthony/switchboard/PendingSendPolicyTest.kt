package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ChannelMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class PendingSendPolicyTest {

	private val t0 = Instant.parse("2026-09-28T12:00:00Z").toEpochMilli()
	private fun isoAt(ms: Long) = Instant.ofEpochMilli(ms).toString()
	private fun cmd(conv: String, text: String, ms: Long) = QueuedCommand(conv, text, isoAt(ms))

	private fun derive(
		queued: Map<String, QueuedCommand> = emptyMap(),
		delivered: Set<String> = emptySet(),
		seen: Map<String, SeenSend> = emptyMap(),
		nowMs: Long = t0,
		connected: Boolean = true,
	) = PendingSendPolicy.derive("c1", queued, delivered, seen, nowMs, connected)

	@Test
	fun freshCommandForThisConversationIsSendingOthersIgnored() {
		val rows = derive(queued = mapOf("k1" to cmd("c1", "hello", t0 - 1000), "k2" to cmd("c2", "other", t0 - 1000)))
		assertEquals(listOf(PendingSend("k1", "hello", isoAt(t0 - 1000), PendingSendState.SENDING)), rows)
	}

	@Test
	fun thresholdsAndPrecedence() {
		fun at(age: Long, connected: Boolean = true) =
			derive(queued = mapOf("k" to cmd("c1", "x", t0 - age)), connected = connected).single().state
		assertEquals(PendingSendState.SENDING, at(19_999))
		assertEquals(PendingSendState.NOT_PICKED_UP, at(20_000))
		assertEquals(PendingSendState.EXPIRED, at(600_000))
		assertEquals(PendingSendState.OFFLINE, at(30_000, connected = false))
		assertEquals(PendingSendState.EXPIRED, at(600_000, connected = false))
	}

	@Test
	fun unparseableIsNotPickedUpAndFutureIsSending() {
		val bad = derive(queued = mapOf("k" to QueuedCommand("c1", "x", "not-a-time")), nowMs = t0 + 3_600_000)
		assertEquals(PendingSendState.NOT_PICKED_UP, bad.single().state)
		val future = derive(queued = mapOf("k" to cmd("c1", "x", t0 + 120_000)))
		assertEquals(PendingSendState.SENDING, future.single().state)
	}

	@Test
	fun deliveredWhileStillQueuedShowsNothing() {
		assertTrue(derive(queued = mapOf("k1" to cmd("c1", "hello", t0 - 1000)), delivered = setOf("k1")).isEmpty())
	}

	@Test
	fun identicalTextsResolveByKey() {
		val rows = derive(
			queued = mapOf("k1" to cmd("c1", "same", t0 - 2000), "k2" to cmd("c1", "same", t0 - 1000)),
			delivered = setOf("k1"),
		)
		assertEquals(listOf("k2"), rows.map { it.key })
	}

	@Test
	fun goneSendIsNotDeliveredOnlyAfterGraceAndWithoutRow() {
		val seen = mapOf("k1" to SeenSend("c1", "lost", isoAt(t0 - 30_000), goneAtMs = t0 - 4_999))
		assertTrue(derive(seen = seen).isEmpty())
		assertEquals(PendingSendState.NOT_DELIVERED, derive(seen = seen, nowMs = t0 + 1).single().state)
		assertTrue(derive(seen = seen, nowMs = t0 + 1, delivered = setOf("k1")).isEmpty())
	}

	@Test
	fun parseQueuedIgnoresMalformedEntries() {
		assertEquals(QueuedCommand("c1", "hi", "t"), PendingSendPolicy.parseQueued(mapOf("conversation_id" to "c1", "text" to "hi", "issued_at" to "t")))
		assertNull(PendingSendPolicy.parseQueued(mapOf("conversation_id" to "c1", "text" to 42)))
		assertNull(PendingSendPolicy.parseQueued(mapOf("text" to "hi")))
		assertNull(PendingSendPolicy.parseQueued("just a string"))
		assertNull(PendingSendPolicy.parseQueued(null))
	}

	@Test
	fun updateSeenStampsGoneAndDropsDelivered() {
		val s1 = PendingSendPolicy.updateSeen(emptyMap(), mapOf("k1" to cmd("c1", "a", t0)), t0, emptySet())
		assertNull(s1.getValue("k1").goneAtMs)
		val s2 = PendingSendPolicy.updateSeen(s1, emptyMap(), t0 + 10, emptySet())
		assertEquals(t0 + 10, s2.getValue("k1").goneAtMs)
		val s3 = PendingSendPolicy.updateSeen(s2, emptyMap(), t0 + 99, emptySet())
		assertEquals(t0 + 10, s3.getValue("k1").goneAtMs)
		assertTrue(PendingSendPolicy.updateSeen(s1, emptyMap(), t0 + 10, setOf("k1")).isEmpty())
	}

	@Test
	fun pruneDeliveredKeepsQueuedEntries() {
		val seen = mapOf(
			"gone" to SeenSend("c1", "a", null, goneAtMs = t0),
			"queued" to SeenSend("c1", "b", null),
		)
		assertEquals(setOf("queued"), PendingSendPolicy.pruneDelivered(seen, setOf("gone", "queued")).keys)
	}

	@Test
	fun needsTickCoversQueuedAndGraceWindow() {
		assertTrue(PendingSendPolicy.needsTick("c1", mapOf("k" to cmd("c1", "x", t0)), emptyMap()))
		assertTrue(PendingSendPolicy.needsTick("c1", emptyMap(), mapOf("k" to SeenSend("c1", "x", null, goneAtMs = t0))))
		assertFalse(PendingSendPolicy.needsTick("c1", mapOf("k" to cmd("c2", "x", t0)), mapOf("k2" to SeenSend("c1", "x", null))))
	}

	@Test
	fun restoreDraftAndCommandIds() {
		assertEquals("back", PendingSendPolicy.restoreDraft("  ", "back"))
		assertEquals("typing\nback", PendingSendPolicy.restoreDraft("typing", "back"))
		val msgs = listOf("m1" to ChannelMessage(command_id = "k1"), "m2" to ChannelMessage())
		assertEquals(setOf("k1"), PendingSendPolicy.commandIdsIn(msgs))
		assertEquals(
			"Server hasn't taken this. It delivers if the server returns within 10 min.",
			PendingSendPolicy.statusText(PendingSendState.NOT_PICKED_UP),
		)
	}
}
