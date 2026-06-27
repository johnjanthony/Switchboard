package io.github.johnjanthony.switchboard.ui.theme

import androidx.compose.ui.graphics.Color

// Switchboard "dialed-down console" palette. Dark only; every accent names a real
// conversation/line state rather than decorating.

// Neutrals - cool charcoal panel.
val Panel  = Color(0xFF14161A)   // screen ground             -> surface / background
val Bay    = Color(0xFF1B1E24)   // raised: bubbles, cards    -> surfaceVariant
val Bay2   = Color(0xFF232730)   // one step up: chips, pressed
val Well   = Color(0xFF0E1014)   // recessed: code blocks, inputs
val Bezel  = Color(0xFF2D323B)   // dividers, borders         -> outline
val Ink    = Color(0xFFE8EBEF)   // primary text              -> onSurface
val InkDim = Color(0xFF889099)   // secondary text / metadata -> onSurfaceVariant

// Functional signal colors.
val Brass     = Color(0xFFC9A24A)   // connected / open / primary action   -> primary
val BrassDeep = Color(0xFF2A2417)   // brass-tinted container (your reply)  -> primaryContainer
val Jade      = Color(0xFF57D9A3)   // live / working                      -> secondary
val Coral     = Color(0xFFFF7A66)   // waiting on you                      -> tertiary
val AlertRed  = Color(0xFFE5484D)   // end / reject / stale                -> error

// Retained: referenced by the message download pill.
val DarkGreyPill = Bay2
