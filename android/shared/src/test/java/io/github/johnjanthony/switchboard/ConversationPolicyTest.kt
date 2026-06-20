package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.ConversationMember
import io.github.johnjanthony.switchboard.network.ConversationRow
import io.github.johnjanthony.switchboard.network.ConversationSummary
import io.github.johnjanthony.switchboard.network.Pending
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
}
