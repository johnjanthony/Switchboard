package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.ConversationSummary
import io.github.johnjanthony.switchboard.network.Pending
import io.github.johnjanthony.switchboard.network.WidgetRing
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationPolicyTest {

	private fun row(
		id: String,
		title: String = "T",
		hidden: Boolean = false,
		lastActivityAt: String = "",
		pending: Map<String, Pending> = emptyMap(),
		members: List<ConversationMember> = emptyList(),
	) = ConversationRow(
		summary = ConversationSummary(
			id = id,
			title = title,
			state = "active",
			members = members,
			lastActivityAt = lastActivityAt,
			hidden = hidden,
		),
		pendingQuestions = pending,
	)

	private fun pending(req: String, cancelled: Boolean = false) =
		Pending(sender = "Claude", requestId = req, questionText = "Q?", cancelled = cancelled, msgId = "m-$req")

	@Test
	fun `sentinel id is synthetic, real conv id is not`() {
		assertTrue(isSyntheticConversation("_admin"))
		assertFalse(isSyntheticConversation("conv-1"))
	}

	@Test
	fun `pendingReplyCount excludes cancelled questions`() {
		val r = row("conv-1", pending = mapOf("a" to pending("a"), "b" to pending("b", cancelled = true)))
		assertEquals(1, pendingReplyCount(r))
		assertTrue(conversationNeedsReply(r))
	}

	@Test
	fun `a row with only cancelled pendings does not need reply`() {
		val r = row("conv-1", pending = mapOf("b" to pending("b", cancelled = true)))
		assertEquals(0, pendingReplyCount(r))
		assertFalse(conversationNeedsReply(r))
	}

	@Test
	fun `partition puts needs-reply first, excludes hidden, orders each by activity desc`() {
		val needs = row("conv-need", lastActivityAt = "2026-06-14T10:00:00Z", pending = mapOf("a" to pending("a")))
		val quietNew = row("conv-quiet-new", lastActivityAt = "2026-06-14T11:00:00Z")
		val quietOld = row("conv-quiet-old", lastActivityAt = "2026-06-14T09:00:00Z")
		val hidden = row("conv-hidden", hidden = true, pending = mapOf("h" to pending("h")))
		val (needsReply, others) = partitionConversationsForWatch(listOf(quietOld, needs, quietNew, hidden))
		assertEquals(listOf("conv-need"), needsReply.map { it.id })
		assertEquals(listOf("conv-quiet-new", "conv-quiet-old"), others.map { it.id })
	}

	@Test
	fun `admin row never needs reply and lands in others`() {
		val admin = row("_admin", title = "Admin")
		val (needsReply, others) = partitionConversationsForWatch(listOf(admin))
		assertTrue(needsReply.isEmpty())
		assertEquals(listOf("_admin"), others.map { it.id })
	}

	@Test
	fun `answerable question detection mirrors the legacy inline rule`() {
		assertTrue(isAnswerableQuestion("ask_human", "m1", emptySet(), cancelled = false, rejected = false))
		assertTrue(isAnswerableQuestion("question", "m1", emptySet(), cancelled = false, rejected = false))
		assertFalse(isAnswerableQuestion("notify", "m1", emptySet(), cancelled = false, rejected = false))
		assertFalse(isAnswerableQuestion("ask_human", "m1", setOf("m1"), cancelled = false, rejected = false))
		assertFalse(isAnswerableQuestion("ask_human", "m1", emptySet(), cancelled = true, rejected = false))
		assertFalse(isAnswerableQuestion("ask_human", "m1", emptySet(), cancelled = false, rejected = true))
	}

	@Test
	fun `bulk respond label uses title, falling back to roster when blank`() {
		assertEquals("Reviewing PR", bulkRespondSectionLabel("Reviewing PR", "Claude, Gemini"))
		assertEquals("Claude, Gemini", bulkRespondSectionLabel("", "Claude, Gemini"))
		assertEquals("Claude", bulkRespondSectionLabel("   ", "Claude"))
	}

	@Test
	fun `attach firebase listeners only when authed and not already attached`() {
		assertTrue(shouldAttachFirebaseListeners(hasAuthedUser = true, alreadyAttached = false))
		assertFalse(shouldAttachFirebaseListeners(hasAuthedUser = false, alreadyAttached = false))
		assertFalse(shouldAttachFirebaseListeners(hasAuthedUser = true, alreadyAttached = true))
		assertFalse(shouldAttachFirebaseListeners(hasAuthedUser = false, alreadyAttached = true))
	}

	private fun member(cliSessionId: String, sender: String = "Claude") =
		ConversationMember(cliSessionId = cliSessionId, sender = sender)

	private fun ring(pct: Double, status: String = "live") =
		WidgetRing(pct = pct, model = "opus", status = status)

	@Test
	fun `ringForMember matches by cliSessionId`() {
		val rings = mapOf("s1" to ring(0.4), "s2" to ring(0.9))
		assertEquals(ring(0.9), ringForMember(member("s2"), rings))
	}

	@Test
	fun `ringForMember returns null when no ring matches`() {
		assertEquals(null, ringForMember(member("nope"), mapOf("s1" to ring(0.4))))
	}

	@Test
	fun `ringForMember returns null when cliSessionId is blank`() {
		assertEquals(null, ringForMember(member(""), mapOf("s1" to ring(0.4))))
	}

	@Test
	fun `ringForMember returns null against an empty rings map`() {
		assertEquals(null, ringForMember(member("s1"), emptyMap()))
	}

	@Test
	fun `ringSeverity matches Watchtower and Operator thresholds`() {
		assertEquals(RingSeverity.RED, ringSeverity(0.85))    // > 0.80
		assertEquals(RingSeverity.AMBER, ringSeverity(0.80))  // not > 0.80, but >= 0.50
		assertEquals(RingSeverity.AMBER, ringSeverity(0.50))
		assertEquals(RingSeverity.GREEN, ringSeverity(0.49))
		assertEquals(RingSeverity.NONE, ringSeverity(null))
	}

	@Test
	fun `listRowContextRing returns the highest-fill member ring when above 50 percent`() {
		val rings = mapOf("s1" to ring(0.62), "s2" to ring(0.83))
		val members = listOf(member("s1"), member("s2"))
		assertEquals(ring(0.83), listRowContextRing(members, rings))
	}

	@Test
	fun `listRowContextRing suppresses fills at or below 50 percent`() {
		val rings = mapOf("s1" to ring(0.40), "s2" to ring(0.50))
		val members = listOf(member("s1"), member("s2"))
		// 0.50 is amber in the popover, but the list stays quiet until strictly above 50%.
		assertEquals(null, listRowContextRing(members, rings))
	}

	@Test
	fun `listRowContextRing returns null when no member has a ring`() {
		assertEquals(null, listRowContextRing(listOf(member("x"), member("y")), mapOf("s1" to ring(0.9))))
	}
}
