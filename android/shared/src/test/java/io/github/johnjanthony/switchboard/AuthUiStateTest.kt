package io.github.johnjanthony.switchboard

import org.junit.Assert.assertEquals
import org.junit.Test

class AuthUiStateTest {
	@Test
	fun `content present hides the empty state`() {
		assertEquals(EmptyStateKind.NONE, emptyStateFor(true, AuthUiState.FAILED))
	}

	@Test
	fun `no content and failed auth shows the retry affordance`() {
		assertEquals(EmptyStateKind.SIGN_IN_FAILED, emptyStateFor(false, AuthUiState.FAILED))
	}

	@Test
	fun `no content and in-progress shows loading`() {
		assertEquals(EmptyStateKind.LOADING, emptyStateFor(false, AuthUiState.IN_PROGRESS))
	}

	@Test
	fun `no content but signed in shows the genuine empty state`() {
		assertEquals(EmptyStateKind.NO_CONVERSATIONS, emptyStateFor(false, AuthUiState.SIGNED_IN))
	}
}
