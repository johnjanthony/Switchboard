package io.github.johnjanthony.switchboard

/** Sign-in state hoisted from the auth flow, so the UI can distinguish a failure from a
 * still-connecting state (REV-206). */
enum class AuthUiState { IN_PROGRESS, SIGNED_IN, FAILED }

/** What the conversation list's empty region should render. */
enum class EmptyStateKind { NONE, LOADING, SIGN_IN_FAILED, NO_CONVERSATIONS }

/**
 * Decide the empty-state to show. With content, render nothing. Otherwise a failed auth
 * gets the retry affordance, an in-progress attempt gets a loading hint, and a signed-in
 * but empty account gets the genuine "no conversations" state.
 */
fun emptyStateFor(hasContent: Boolean, auth: AuthUiState): EmptyStateKind = when {
	hasContent -> EmptyStateKind.NONE
	auth == AuthUiState.FAILED -> EmptyStateKind.SIGN_IN_FAILED
	auth == AuthUiState.IN_PROGRESS -> EmptyStateKind.LOADING
	else -> EmptyStateKind.NO_CONVERSATIONS
}
