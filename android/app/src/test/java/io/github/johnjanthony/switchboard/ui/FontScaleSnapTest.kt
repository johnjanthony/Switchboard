package io.github.johnjanthony.switchboard.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class FontScaleSnapTest {
	@Test
	fun `snaps mid-step value to nearest 0_05 step`() {
		assertEquals(1.25f, snapFontScale(1.247f), 0.0001f)
	}

	@Test
	fun `snaps already-on-step value to itself`() {
		assertEquals(1.5f, snapFontScale(1.5f), 0.0001f)
	}

	@Test
	fun `clamps below-min to 1_0`() {
		assertEquals(1.0f, snapFontScale(0.8f), 0.0001f)
		assertEquals(1.0f, snapFontScale(0.0f), 0.0001f)
		assertEquals(1.0f, snapFontScale(-1.0f), 0.0001f)
	}

	@Test
	fun `clamps above-max to 2_5`() {
		assertEquals(2.5f, snapFontScale(2.51f), 0.0001f)
		assertEquals(2.5f, snapFontScale(3.0f), 0.0001f)
		assertEquals(2.5f, snapFontScale(100.0f), 0.0001f)
	}

	@Test
	fun `returns endpoint values unchanged`() {
		assertEquals(1.0f, snapFontScale(1.0f), 0.0001f)
		assertEquals(2.5f, snapFontScale(2.5f), 0.0001f)
	}
}
