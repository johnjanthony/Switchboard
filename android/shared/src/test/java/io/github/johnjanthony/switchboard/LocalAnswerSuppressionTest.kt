package io.github.johnjanthony.switchboard

import org.junit.Assert.assertEquals
import org.junit.Test

class LocalAnswerSuppressionTest {

	@Test
	fun `answered id still listed in consecutive snapshots stays suppressed`() {
		val s = LocalAnswerSuppression()
		s.add("conv-1", "req-a")
		val snapshot = mapOf("req-a" to "pending")
		assertEquals(emptyMap<String, String>(), s.reconcile("conv-1", snapshot, snapshot))
	}

	@Test
	fun `suppression drops once the server deletes the entry and a re-ask surfaces`() {
		val s = LocalAnswerSuppression()
		s.add("conv-1", "req-a")
		val listed = mapOf("req-a" to "pending")
		s.reconcile("conv-1", listed, listed)
		s.reconcile("conv-1", emptyMap(), listed)
		val reAsk = mapOf("req-a" to "pending2")
		assertEquals(reAsk, s.reconcile("conv-1", reAsk, emptyMap()))
	}

	@Test
	fun `fresh appearance not in previous snapshot is not suppressed`() {
		val s = LocalAnswerSuppression()
		s.add("conv-1", "req-a")
		val parsed = mapOf("req-a" to "pending")
		assertEquals(parsed, s.reconcile("conv-1", parsed, emptyMap()))
	}

	@Test
	fun `clear removes a conversation's suppression state`() {
		val s = LocalAnswerSuppression()
		s.add("conv-1", "req-a")
		s.clear("conv-1")
		val parsed = mapOf("req-a" to "pending")
		assertEquals(parsed, s.reconcile("conv-1", parsed, parsed))
	}

	@Test
	fun `suppression in one conversation does not affect another`() {
		val s = LocalAnswerSuppression()
		s.add("conv-1", "req-a")
		val parsed = mapOf("req-a" to "pending")
		assertEquals(parsed, s.reconcile("conv-2", parsed, parsed))
	}
}
