package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.SpawnCliOptions
import io.github.johnjanthony.switchboard.network.SpawnModelOption
import io.github.johnjanthony.switchboard.network.SpawnOptions
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SpawnOptionsPolicyTest {

	private val tiers = listOf("low", "medium", "high", "xhigh", "max")

	private fun options() = SpawnOptions(
		publishedAt = "2026-07-30T00:00:00Z",
		claude = SpawnCliOptions(models = listOf(
			SpawnModelOption(id = "fable", efforts = tiers),
			SpawnModelOption(id = "opus", efforts = tiers),
			SpawnModelOption(id = "sonnet", efforts = tiers),
			SpawnModelOption(id = "haiku", efforts = emptyList()),
		)),
		antigravity = SpawnCliOptions(models = listOf(
			SpawnModelOption(id = "gemini-3.6-flash-high", efforts = emptyList()),
			SpawnModelOption(id = "claude-sonnet-4-6", efforts = emptyList()),
		)),
	)

	@Test
	fun modelOptionsKeyedToAgent() {
		assertEquals(listOf("fable", "opus", "sonnet", "haiku"), SpawnOptionsPolicy.modelOptions(options(), "claude"))
		assertEquals(
			listOf("gemini-3.6-flash-high", "claude-sonnet-4-6"),
			SpawnOptionsPolicy.modelOptions(options(), "antigravity"),
		)
	}

	@Test
	fun modelOptionsEmptyWhenCatalogAbsent() {
		assertTrue(SpawnOptionsPolicy.modelOptions(null, "claude").isEmpty())
	}

	@Test
	fun effortOptionsForClaudeModel() {
		assertEquals(tiers, SpawnOptionsPolicy.effortOptions(options(), "claude", "sonnet"))
		assertTrue(SpawnOptionsPolicy.effortOptions(options(), "claude", "haiku").isEmpty())
	}

	@Test
	fun effortOptionsWithoutModelIsTierUnionInOrder() {
		assertEquals(tiers, SpawnOptionsPolicy.effortOptions(options(), "claude", null))
	}

	@Test
	fun effortOptionsAlwaysEmptyForAntigravity() {
		assertTrue(SpawnOptionsPolicy.effortOptions(options(), "antigravity", "gemini-3.6-flash-high").isEmpty())
		assertFalse(SpawnOptionsPolicy.showEffortPicker("antigravity"))
		assertTrue(SpawnOptionsPolicy.showEffortPicker("claude"))
	}

	// Realtime Database does not store empty arrays, so an effort-less model publishes with
	// NO efforts key at all and the DTO deserializes it to null. That is not an edge case:
	// it is what every haiku and every Antigravity entry actually looks like on the wire,
	// so these fixtures mirror the live node rather than the tidy in-memory catalog.
	private fun liveShapeOptions() = SpawnOptions(
		publishedAt = "2026-07-30T19:08:13Z",
		claude = SpawnCliOptions(models = listOf(
			SpawnModelOption(id = "fable", efforts = tiers),
			SpawnModelOption(id = "opus", efforts = tiers),
			SpawnModelOption(id = "sonnet", efforts = tiers),
			SpawnModelOption(id = "haiku", efforts = null),
		)),
		antigravity = SpawnCliOptions(models = listOf(
			SpawnModelOption(id = "gemini-3.6-flash-high", efforts = null),
			SpawnModelOption(id = "claude-sonnet-4-6", efforts = null),
		)),
	)

	@Test
	fun absentEffortsKeyDegradesToEmptyList() {
		assertTrue(SpawnOptionsPolicy.effortOptions(liveShapeOptions(), "claude", "haiku").isEmpty())
		assertTrue(SpawnOptionsPolicy.effortOptions(liveShapeOptions(), "antigravity", "gemini-3.6-flash-high").isEmpty())
	}

	@Test
	fun absentEffortsKeyIsSkippedInTheUnionAndDoesNotSuppressRealTiers() {
		assertEquals(tiers, SpawnOptionsPolicy.effortOptions(liveShapeOptions(), "claude", null))
	}

	@Test
	fun absentEffortsKeyStillListsTheModelIds() {
		assertEquals(
			listOf("fable", "opus", "sonnet", "haiku"),
			SpawnOptionsPolicy.modelOptions(liveShapeOptions(), "claude"),
		)
		assertEquals(
			listOf("gemini-3.6-flash-high", "claude-sonnet-4-6"),
			SpawnOptionsPolicy.modelOptions(liveShapeOptions(), "antigravity"),
		)
	}

	@Test
	fun absentModelsKeyDegradesToEmptyLists() {
		val noModels = SpawnOptions(
			publishedAt = "2026-07-30T19:08:13Z",
			claude = SpawnCliOptions(models = null),
			antigravity = SpawnCliOptions(models = null),
		)
		assertTrue(SpawnOptionsPolicy.modelOptions(noModels, "claude").isEmpty())
		assertTrue(SpawnOptionsPolicy.modelOptions(noModels, "antigravity").isEmpty())
		assertTrue(SpawnOptionsPolicy.effortOptions(noModels, "claude", null).isEmpty())
		assertTrue(SpawnOptionsPolicy.effortOptions(noModels, "claude", "sonnet").isEmpty())
	}
}
