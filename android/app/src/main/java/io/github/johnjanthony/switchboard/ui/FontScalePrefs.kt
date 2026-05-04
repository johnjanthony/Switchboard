package io.github.johnjanthony.switchboard.ui

import android.content.Context

private const val PREFS_FILE = "switchboard_prefs"
private const val KEY_FEED = "font_scale_feed"
private const val KEY_VIEWER = "font_scale_viewer"

private fun Context.prefs() = getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)

fun Context.feedFontScale(): Float = prefs().getFloat(KEY_FEED, 1f)

fun Context.setFeedFontScale(scale: Float) {
	prefs().edit().putFloat(KEY_FEED, scale).apply()
}

fun Context.viewerFontScale(): Float = prefs().getFloat(KEY_VIEWER, 1f)

fun Context.setViewerFontScale(scale: Float) {
	prefs().edit().putFloat(KEY_VIEWER, scale).apply()
}
