package io.github.johnjanthony.switchboard

/**
 * Whole-app foreground state, set by the ProcessLifecycleOwner observer in
 * SwitchboardApplication. A plain holder rather than a lifecycle-aware type so
 * the shared view model can read it without an Android lifecycle dependency in
 * unit tests. Starts false; ON_START flips it before any UI interaction.
 */
object AppForeground {
	@Volatile
	var isForeground: Boolean = false
}
