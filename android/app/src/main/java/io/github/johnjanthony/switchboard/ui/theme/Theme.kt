package io.github.johnjanthony.switchboard.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// Roles re-pointed from the stock Material blue/teal to the console palette:
// primary = Brass (connected/open/action), secondary = Jade (live),
// tertiary = Coral (waiting on you), error = AlertRed (destructive). The deliberate
// surface/background inversion from the starter theme is dropped - both are Panel.
private val DarkColorScheme = darkColorScheme(
	primary             = Brass,
	onPrimary           = Panel,
	primaryContainer    = BrassDeep,
	onPrimaryContainer  = Ink,
	secondary           = Jade,
	onSecondary         = Panel,
	tertiary            = Coral,
	onTertiary          = Panel,
	error               = AlertRed,
	onError             = Panel,
	background          = Panel,
	onBackground        = Ink,
	surface             = Panel,
	onSurface           = Ink,
	surfaceVariant      = Bay,
	onSurfaceVariant    = InkDim,
	outline             = Bezel,
)

@Composable
fun SwitchboardTheme(
	darkTheme: Boolean = true, // Force dark theme by default
	dynamicColor: Boolean = false, // Disable dynamic color for consistent theme
	content: @Composable () -> Unit
) {
	val colorScheme = DarkColorScheme
	val view = LocalView.current
	if (!view.isInEditMode) {
		SideEffect {
			val window = (view.context as Activity).window
			window.statusBarColor = Panel.toArgb()
			WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = false
		}
	}

	MaterialTheme(
		colorScheme = colorScheme,
		typography = Typography,
		content = content
	)
}
