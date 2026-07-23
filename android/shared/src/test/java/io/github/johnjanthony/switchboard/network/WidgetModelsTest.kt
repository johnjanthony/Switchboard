package io.github.johnjanthony.switchboard.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class WidgetModelsTest {

	// Firebase getValue(Class) requires a no-arg constructor with sane defaults;
	// these assertions guard that contract (a missing default would break parsing
	// silently at runtime on the device, where there is no unit test).
	@Test
	fun `widget ring is constructible with no args and sane defaults`() {
		val r = WidgetRing()
		assertEquals(0.0, r.pct, 0.0)
		assertEquals("", r.model)
		assertEquals("", r.status)
		assertEquals(0L, r.contextTokens)
		assertEquals(0L, r.window)
		assertFalse(r.isError)
	}

	@Test
	fun `widget ring carries its values`() {
		val r = WidgetRing(pct = 0.83, model = "claude-opus-4-8", status = "live",
			contextTokens = 166000L, window = 200000L, isError = false)
		assertEquals(0.83, r.pct, 0.0)
		assertEquals("live", r.status)
		assertEquals(166000L, r.contextTokens)
	}

	@Test
	fun `widget quota nests two windows and is no-arg constructible`() {
		val q = WidgetQuota()
		assertEquals(null, q.session)
		assertEquals(null, q.weekly)
		assertEquals(null, q.antigravity)
		assertEquals("", q.polledAt)
		val group = WidgetQuotaGroup(
			displayName = "Gemini Models",
			session = WidgetQuotaWindow(pct = 0.25, resetsAt = "2026-06-26T20:00:00Z"),
			weekly = WidgetQuotaWindow(pct = 0.50, resetsAt = "2026-06-30T00:00:00Z"),
		)
		val filled = WidgetQuota(
			session = WidgetQuotaWindow(pct = 0.42, resetsAt = "2026-06-26T20:00:00Z"),
			weekly = WidgetQuotaWindow(pct = 0.18, resetsAt = "2026-06-30T00:00:00Z"),
			polledAt = "2026-06-26T15:00:00Z",
			antigravity = listOf(group),
		)
		assertEquals(0.42, filled.session!!.pct, 0.0)
		assertEquals("2026-06-30T00:00:00Z", filled.weekly!!.resetsAt)
		assertEquals(1, filled.antigravity!!.size)
		assertEquals("Gemini Models", filled.antigravity!![0].displayName)
		assertEquals(0.25, filled.antigravity!![0].session!!.pct, 0.0)
	}

	@Test
	fun `widget status defaults to unknown idle and is no-arg constructible`() {
		val s = WidgetStatus()
		assertEquals("unknown", s.level)
		assertEquals("idle", s.watchState)
		assertEquals("check", s.button)
		assertFalse(s.dotVisible)
		assertFalse(s.hasData)
	}
}
