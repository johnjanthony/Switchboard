package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.SpawnCliOptions
import io.github.johnjanthony.switchboard.network.SpawnModelOption
import io.github.johnjanthony.switchboard.network.SpawnOptions
import org.junit.Assert.assertEquals
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
	)

	@Test
	fun modelOptionsListTheClaudeCatalog() {
		assertEquals(listOf("fable", "opus", "sonnet", "haiku"), SpawnOptionsPolicy.modelOptions(options()))
	}

	@Test
	fun modelOptionsEmptyWhenCatalogAbsent() {
		assertTrue(SpawnOptionsPolicy.modelOptions(null).isEmpty())
	}

	@Test
	fun effortOptionsForClaudeModel() {
		assertEquals(tiers, SpawnOptionsPolicy.effortOptions(options(), "sonnet"))
		assertTrue(SpawnOptionsPolicy.effortOptions(options(), "haiku").isEmpty())
	}

	@Test
	fun effortOptionsWithoutModelIsTierUnionInOrder() {
		assertEquals(tiers, SpawnOptionsPolicy.effortOptions(options(), null))
	}

	// Realtime Database does not store empty arrays, so an effort-less model publishes with
	// NO efforts key at all and the DTO deserializes it to null. That is not an edge case:
	// it is what every haiku entry actually looks like on the wire,
	// so these fixtures mirror the live node rather than the tidy in-memory catalog.
	private fun liveShapeOptions() = SpawnOptions(
		publishedAt = "2026-07-30T19:08:13Z",
		claude = SpawnCliOptions(models = listOf(
			SpawnModelOption(id = "fable", efforts = tiers),
			SpawnModelOption(id = "opus", efforts = tiers),
			SpawnModelOption(id = "sonnet", efforts = tiers),
			SpawnModelOption(id = "haiku", efforts = null),
		)),
	)

	@Test
	fun absentEffortsKeyDegradesToEmptyList() {
		assertTrue(SpawnOptionsPolicy.effortOptions(liveShapeOptions(), "haiku").isEmpty())
	}

	@Test
	fun absentEffortsKeyIsSkippedInTheUnionAndDoesNotSuppressRealTiers() {
		assertEquals(tiers, SpawnOptionsPolicy.effortOptions(liveShapeOptions(), null))
	}

	@Test
	fun absentEffortsKeyStillListsTheModelIds() {
		assertEquals(
			listOf("fable", "opus", "sonnet", "haiku"),
			SpawnOptionsPolicy.modelOptions(liveShapeOptions()),
		)
	}

	@Test
	fun absentModelsKeyDegradesToEmptyLists() {
		val noModels = SpawnOptions(
			publishedAt = "2026-07-30T19:08:13Z",
			claude = SpawnCliOptions(models = null),
		)
		assertTrue(SpawnOptionsPolicy.modelOptions(noModels).isEmpty())
		assertTrue(SpawnOptionsPolicy.effortOptions(noModels, null).isEmpty())
		assertTrue(SpawnOptionsPolicy.effortOptions(noModels, "sonnet").isEmpty())
	}
}
