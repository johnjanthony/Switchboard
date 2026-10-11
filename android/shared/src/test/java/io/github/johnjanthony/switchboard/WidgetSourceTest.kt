package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.QuotaNode
import io.github.johnjanthony.switchboard.network.QuotaNodeWindow
import io.github.johnjanthony.switchboard.network.RegistrySession
import io.github.johnjanthony.switchboard.network.WidgetQuota
import io.github.johnjanthony.switchboard.network.WidgetQuotaWindow
import io.github.johnjanthony.switchboard.network.WidgetRing
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WidgetSourceTest {

	private fun rec(
		state: String = "idle",
		pct: Double? = 0.41,
		tokens: Long? = 410_000,
		window: Long? = 1_000_000,
		model: String? = "claude-opus-5-5",
	) = RegistrySession(cliSessionId = "s1", state = state, contextPct = pct, contextTokens = tokens, contextWindow = window, model = model)

	@Test
	fun `a live session's ring carries its context and model`() {
		assertEquals(
			mapOf("s1" to WidgetRing(pct = 0.41, model = "claude-opus-5-5", status = "live", contextTokens = 410_000, window = 1_000_000, isError = false)),
			ringsFromSessions(mapOf("s1" to rec(state = "active"))),
		)
	}

	@Test
	fun `waiting states count as live and idle stays idle`() {
		val rings = ringsFromSessions(mapOf("a" to rec(state = "awaiting_human"), "b" to rec(state = "awaiting_agent"), "c" to rec(state = "idle")))
		assertEquals(listOf("live", "live", "idle"), listOf("a", "b", "c").map { rings.getValue(it).status })
	}

	@Test
	fun `ended and lost sessions get no ring`() {
		assertEquals(emptyMap<String, WidgetRing>(), ringsFromSessions(mapOf("e" to rec(state = "ended"), "l" to rec(state = "lost"))))
	}

	@Test
	fun `a session that has not reported its context gets no ring`() {
		assertEquals(emptyMap<String, WidgetRing>(), ringsFromSessions(mapOf("s1" to rec(pct = null, tokens = null, window = null))))
	}

	@Test
	fun `missing token counts and model fall back to zero and blank`() {
		val ring = ringsFromSessions(mapOf("s1" to rec(tokens = null, window = null, model = null))).getValue("s1")
		assertEquals(Triple(0L, 0L, ""), Triple(ring.contextTokens, ring.window, ring.model))
	}

	@Test
	fun `quota percent becomes the fraction the screens draw`() {
		val node = QuotaNode(
			fiveHour = QuotaNodeWindow(percentUsed = 23.5, resetsAt = "2026-10-10T18:00:00Z"),
			sevenDay = QuotaNodeWindow(percentUsed = 61.0, resetsAt = "2026-10-14T09:00:00Z"),
			receivedAt = "2026-10-10T17:02:11+00:00",
		)
		assertEquals(
			WidgetQuota(
				session = WidgetQuotaWindow(pct = 0.235, resetsAt = "2026-10-10T18:00:00Z"),
				weekly = WidgetQuotaWindow(pct = 0.61, resetsAt = "2026-10-14T09:00:00Z"),
				polledAt = "2026-10-10T17:02:11+00:00",
				antigravity = null,
			),
			quotaFromNode(node),
		)
	}

	@Test
	fun `a window the server zeroed after its reset shows 0 with no countdown`() {
		val quota = quotaFromNode(QuotaNode(fiveHour = QuotaNodeWindow(percentUsed = 0.0, resetsAt = null), receivedAt = "t"))!!
		assertEquals(WidgetQuotaWindow(pct = 0.0, resetsAt = ""), quota.session)
		assertNull(quota.weekly)
	}

	@Test
	fun `no node or no windows means no quota`() {
		assertNull(quotaFromNode(null))
		assertNull(quotaFromNode(QuotaNode(receivedAt = "t")))
	}
}
